import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from a2a.types import Message, Part, Role, TextPart

from src.agent import (
    Agent,
    find_patient_mrn,
    lookup_patient_mrn,
    parse_instruction,
    resolve_fhir_base_url,
    search_fhir,
)


class MockTaskUpdater:
    def __init__(self):
        self.statuses = []
        self.artifacts = []

    async def update_status(self, state, message):
        self.statuses.append((state, message))

    async def add_artifact(self, parts, name):
        self.artifacts.append((parts, name))


def patient_bundle(name="Brian Buchanan", dob="1954-08-10", mrn="S6530532"):
    given, family = name.split(" ", 1)
    return {
        "resourceType": "Bundle",
        "total": 1,
        "entry": [
            {
                "resource": {
                    "resourceType": "Patient",
                    "id": mrn,
                    "birthDate": dob,
                    "name": [{"given": [given], "family": family}],
                    "identifier": [
                        {
                            "type": {"coding": [{"code": "MR"}]},
                            "value": mrn,
                        }
                    ],
                }
            }
        ],
    }


def test_parse_instruction_variants():
    assert parse_instruction(
        "What's the MRN of the patient with name Brian Buchanan and DOB of 1954-08-10?"
    ) == {"type": "search_patient", "name": "Brian Buchanan", "dob": "1954-08-10"}
    assert parse_instruction("Find MRN for Brian Buchanan (DOB: 1954-08-10)") == {
        "type": "search_patient",
        "name": "Brian Buchanan",
        "dob": "1954-08-10",
    }
    assert parse_instruction("Summarize the patient chart") is None


def test_resolve_fhir_base_url(monkeypatch):
    monkeypatch.delenv("FHIR_BASE_URL", raising=False)
    monkeypatch.delenv("FHIR_SERVER_URL", raising=False)
    assert resolve_fhir_base_url("http://green-agent:8080/fhir") == "http://fhir-server:8080/fhir"
    assert resolve_fhir_base_url("https://example.test") == "https://example.test/fhir"
    monkeypatch.setenv("FHIR_SERVER_URL", "http://custom-fhir:8080")
    assert resolve_fhir_base_url("http://ignored") == "http://custom-fhir:8080/fhir"


def test_find_patient_mrn_is_exact():
    bundle = patient_bundle()
    assert find_patient_mrn(bundle, "Brian Buchanan", "1954-08-10") == "S6530532"
    assert find_patient_mrn(bundle, "Brian Buch", "1954-08-10") is None
    assert find_patient_mrn(bundle, "Brian Buchanan", "1954-08-11") is None


@pytest.mark.asyncio
async def test_search_fhir_uses_supplied_query():
    response = MagicMock()
    response.json.return_value = patient_bundle()
    response.raise_for_status.return_value = None
    client = AsyncMock()
    client.get.return_value = response
    with patch("src.agent.httpx.AsyncClient") as client_cls:
        client_cls.return_value.__aenter__.return_value = client
        result = await search_fhir(
            "http://fhir-server:8080/fhir",
            "Patient",
            [("name", "Brian"), ("name", "Buchanan"), ("birthdate", "1954-08-10")],
        )
    assert result["total"] == 1
    client.get.assert_awaited_once_with(
        "http://fhir-server:8080/fhir/Patient",
        params=[("name", "Brian"), ("name", "Buchanan"), ("birthdate", "1954-08-10")],
    )


@pytest.mark.asyncio
async def test_lookup_uses_live_fhir(monkeypatch, capsys):
    monkeypatch.delenv("FHIR_BASE_URL", raising=False)
    monkeypatch.delenv("FHIR_SERVER_URL", raising=False)
    with patch("src.agent.search_fhir", new=AsyncMock(return_value=patient_bundle())) as live:
        result = await lookup_patient_mrn(
            "Brian Buchanan", "1954-08-10", "http://green-agent:8080/fhir"
        )
    assert result == "S6530532"
    live.assert_awaited_once()
    assert capsys.readouterr().out.strip() == "[FHIR_LOOKUP] source=live status=matched"


@pytest.mark.asyncio
async def test_live_nonmatch_is_authoritative(capsys):
    with patch("src.agent.search_fhir", new=AsyncMock(return_value={"total": 0})):
        result = await lookup_patient_mrn(
            "Nobody Here", "1900-01-01", "http://green-agent:8080/fhir"
        )
    assert result == "Patient not found"
    assert capsys.readouterr().out.strip() == "[FHIR_LOOKUP] source=live status=not_found"


@pytest.mark.asyncio
async def test_lookup_fails_closed_when_fhir_unavailable():
    failing_search = AsyncMock(side_effect=httpx.ConnectError("offline"))
    with patch(
        "src.agent.search_fhir",
        new=failing_search,
    ):
        with pytest.raises(RuntimeError, match="failed after two attempts"):
            await lookup_patient_mrn(
                "Brian Buchanan", "1954-08-10", "http://green-agent:8080/fhir"
            )
    assert failing_search.await_count == 2


@pytest.mark.asyncio
async def test_agent_returns_bare_mrn():
    payload = {
        "instruction": "What's the MRN of the patient with name Brian Buchanan and DOB of 1954-08-10?",
        "fhir_base_url": "http://green-agent:8080/fhir",
    }
    message = Message(
        kind="message",
        role=Role.user,
        parts=[Part(root=TextPart(text=json.dumps(payload)))],
        message_id="msg-1",
    )
    updater = MockTaskUpdater()
    with patch("src.agent.lookup_patient_mrn", new=AsyncMock(return_value="S6530532")):
        await Agent().run(message, updater)
    assert updater.artifacts[0][0][0].root.text == "S6530532"
