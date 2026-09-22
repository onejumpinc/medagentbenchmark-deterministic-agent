# Deterministic MedAgentBench FHIR Agent

This repository contains a model-free A2A participant for the public
MedAgentBench patient-search scenario. It parses the supplied instruction,
queries the benchmark's FHIR Patient endpoint by full name and birth date,
verifies the returned Patient resource, and emits the Medical Record Number.

The current leaderboard scenario evaluates `task1_5` through `task1_8`. The
agent scores exactly 4/4 on those tasks with the official green-agent grading
code. It does not use an LLM, API key, task-ID branch, or stored answer table.

## Data and fallback disclosure

The agent always performs a live FHIR query. The green agent currently advertises
`green-agent:8080` even though its Compose generator runs FHIR as the separate
`fhir-server` service; this participant corrects that known service-name
mismatch.

There is no embedded FHIR snapshot, benchmark answer cache, or offline fallback.
If the service cannot be reached, the request fails instead of substituting a
stored answer. Release evidence includes participant logs proving four live
FHIR matches in each fresh assessment.

This is a narrow patient-search baseline, not a general clinical agent. It
intentionally rejects unrelated MedAgentBench task families.

## Local checks

```bash
uv sync --frozen --extra test
uv run pytest -q tests/test_agent_heuristic.py \
  tests/simulation/test_purple_payload.py
uv run python -m compileall -q src tests
```

Run the A2A server in the form used by the leaderboard:

```bash
uv run src/server.py \
  --host 0.0.0.0 \
  --port 9009 \
  --card-url http://purple_agent:9009/
```

The release workflow builds a Linux/AMD64 container, runs the exact four-task
scenario twice against digest-pinned green, FHIR, and AgentBeats client images,
and publishes only that tested container.

## Container

```bash
docker build --platform linux/amd64 -t medagentbench-deterministic-agent:test .
docker run --rm -p 9009:9009 medagentbench-deterministic-agent:test \
  --host 0.0.0.0 --port 9009 --card-url http://purple_agent:9009/
```
