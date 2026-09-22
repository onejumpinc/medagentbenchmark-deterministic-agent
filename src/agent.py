"""Deterministic FHIR participant for the MedAgentBench patient-search task."""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
from a2a.server.tasks import TaskUpdater
from a2a.types import Message, Part, TaskState, TextPart
from a2a.utils import get_message_text, new_agent_text_message


_PATIENT_SEARCH_PATTERNS = (
    re.compile(
        r"\bname\s+(?P<name>[\w'\-. ]+?)\s+and\s+DOB\s+(?:of\s+)?"
        r"(?P<dob>\d{4}-\d{2}-\d{2})\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:find|lookup|look\s+up)\s+(?:the\s+)?MRN\s+(?:for|of)\s+"
        r"(?P<name>[\w'\-. ]+?)\s*\(DOB\s*:\s*"
        r"(?P<dob>\d{4}-\d{2}-\d{2})\)",
        re.IGNORECASE,
    ),
)

def parse_instruction(text: str) -> dict[str, str] | None:
    """Parse a patient-name and date-of-birth lookup without relying on task IDs."""

    for pattern in _PATIENT_SEARCH_PATTERNS:
        match = pattern.search(text or "")
        if match:
            return {
                "type": "search_patient",
                "name": " ".join(match.group("name").split()),
                "dob": match.group("dob"),
            }
    return None


def _ensure_fhir_path(url: str) -> str:
    parsed = urlparse(url.strip())
    path = parsed.path.rstrip("/")
    if not path.lower().endswith("/fhir"):
        path = f"{path}/fhir" if path else "/fhir"
    return urlunparse(parsed._replace(path=path, params="", query="", fragment=""))


def resolve_fhir_base_url(requested_url: str | None) -> str | None:
    """Resolve the reachable FHIR endpoint inside the benchmark Compose network."""

    configured = os.getenv("FHIR_BASE_URL") or os.getenv("FHIR_SERVER_URL")
    if configured:
        return _ensure_fhir_path(configured)
    if not requested_url:
        return None

    parsed = urlparse(requested_url)
    # The current green agent advertises itself as the FHIR callback host even
    # though the FHIR service is a separate Compose service.
    if parsed.hostname == "green-agent" and parsed.port == 8080:
        return "http://fhir-server:8080/fhir"
    return _ensure_fhir_path(requested_url)


async def search_fhir(
    base_url: str,
    resource_type: str,
    params: dict[str, str] | list[tuple[str, str]],
) -> dict[str, Any]:
    """Return a decoded FHIR search bundle."""

    url = f"{base_url.rstrip('/')}/{resource_type}"
    timeout = httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("FHIR response must be a JSON object")
    return payload


def _normalized_words(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9]+", value.casefold()))


def _patient_full_names(resource: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for human_name in resource.get("name", []):
        if not isinstance(human_name, dict):
            continue
        given = human_name.get("given", [])
        if isinstance(given, str):
            given = [given]
        family = str(human_name.get("family", ""))
        names.append(" ".join([*(str(part) for part in given), family]).strip())
    return names


def _extract_mrn(resource: dict[str, Any]) -> str | None:
    for identifier in resource.get("identifier", []):
        if not isinstance(identifier, dict):
            continue
        codes = {
            str(coding.get("code", "")).upper()
            for coding in (identifier.get("type", {}).get("coding", []) or [])
            if isinstance(coding, dict)
        }
        value = str(identifier.get("value", "")).strip()
        if value and ("MR" in codes or re.fullmatch(r"S\d{7}", value)):
            return value

    resource_id = str(resource.get("id", "")).strip()
    return resource_id if re.fullmatch(r"S\d{7}", resource_id) else None


def find_patient_mrn(bundle: dict[str, Any], name: str, dob: str) -> str | None:
    """Find one exact patient by normalized full name and birth date."""

    target_name = _normalized_words(name)
    matches: list[str] = []
    for entry in bundle.get("entry", []) or []:
        if not isinstance(entry, dict):
            continue
        resource = entry.get("resource", {})
        if not isinstance(resource, dict) or resource.get("resourceType") != "Patient":
            continue
        if resource.get("birthDate") != dob:
            continue
        if not any(_normalized_words(candidate) == target_name for candidate in _patient_full_names(resource)):
            continue
        mrn = _extract_mrn(resource)
        if mrn:
            matches.append(mrn)

    unique_matches = sorted(set(matches))
    return unique_matches[0] if len(unique_matches) == 1 else None


async def lookup_patient_mrn(name: str, dob: str, requested_url: str | None) -> str:
    """Query the benchmark FHIR service and return its authoritative result."""

    base_url = resolve_fhir_base_url(requested_url)
    if not base_url:
        raise RuntimeError("FHIR endpoint was not provided")

    name_parts = name.split()
    params = [("name", part) for part in name_parts]
    params.append(("birthdate", dob))
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            bundle = await search_fhir(base_url, "Patient", params)
            mrn = find_patient_mrn(bundle, name, dob)
            if mrn:
                print("[FHIR_LOOKUP] source=live status=matched", flush=True)
                return mrn
            print("[FHIR_LOOKUP] source=live status=not_found", flush=True)
            return "Patient not found"
        except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt == 0:
                await asyncio.sleep(0.2)

    raise RuntimeError("FHIR patient search failed after two attempts") from last_error


class Agent:
    """A model-free participant that performs exact FHIR patient searches."""

    async def run(self, message: Message, updater: TaskUpdater, task: Any = None) -> None:
        del task
        input_text = get_message_text(message)
        try:
            payload = json.loads(input_text)
        except json.JSONDecodeError:
            payload = {"instruction": input_text}

        if not isinstance(payload, dict):
            payload = {"instruction": input_text}
        instruction = str(payload.get("instruction", input_text))
        parsed = parse_instruction(instruction)

        await updater.update_status(
            TaskState.working,
            new_agent_text_message("Searching the FHIR patient index."),
        )

        if parsed is None:
            response_text = "Unsupported task: expected a patient name and date of birth."
        else:
            response_text = await lookup_patient_mrn(
                parsed["name"],
                parsed["dob"],
                payload.get("fhir_base_url"),
            )

        await updater.add_artifact(
            parts=[Part(root=TextPart(text=response_text))],
            name="FHIR patient-search result",
        )
