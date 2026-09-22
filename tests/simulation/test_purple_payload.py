import json
from unittest.mock import AsyncMock, patch

import pytest
from a2a.types import Message, Part, Role, TextPart

from src.agent import Agent


class MockUpdater:
    def __init__(self):
        self.statuses = []
        self.artifacts = []

    async def update_status(self, state, message):
        self.statuses.append((state, message))

    async def add_artifact(self, parts, name):
        self.artifacts.append((parts, name))


@pytest.mark.asyncio
async def test_agent_payload_fhir_flow():
    payload = {
        "instruction": (
            "What's the MRN of the patient with name Brian Buchanan "
            "and DOB of 1954-08-10?"
        ),
        "system_context": "",
        "fhir_base_url": "http://green-agent:8080/fhir",
        "interaction_limit": 5,
    }
    message = Message(
        kind="message",
        role=Role.user,
        parts=[Part(root=TextPart(text=json.dumps(payload)))],
        message_id="test-id",
        context_id="ctx-id",
    )
    updater = MockUpdater()

    with patch(
        "src.agent.lookup_patient_mrn",
        new=AsyncMock(return_value="S6530532"),
    ) as lookup:
        await Agent().run(message, updater)

    lookup.assert_awaited_once_with(
        "Brian Buchanan",
        "1954-08-10",
        "http://green-agent:8080/fhir",
    )
    assert updater.artifacts[0][0][0].root.text == "S6530532"
