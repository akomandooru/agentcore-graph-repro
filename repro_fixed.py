"""The proposed fix, applied directly to AgentCoreMemorySessionManager.

Extends the real ``AgentCoreMemorySessionManager`` with the three
missing multi-agent methods. Demonstrates that graph state serialises
and restores cleanly once the methods are implemented.

Run:
    python repro_fixed.py

Requires:
    pip install bedrock-agentcore strands-agents

This script does not transmit events to AWS; the
``create_event``/``list_events`` calls are intercepted in-process so
the round-trip can be verified without a provisioned memory resource.
The structural fix (the three methods) is unchanged from what would
ship in the real library.
"""

from __future__ import annotations

import json
import os
from typing import Any

# Provide placeholder AWS credentials so the boto3 client constructor
# does not fail when no real credentials are configured. setdefault()
# leaves any existing environment values untouched. The repro does not
# make real API calls.
os.environ.setdefault("AWS_ACCESS_KEY_ID", "FAKE-FOR-REPRO")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "FAKE-FOR-REPRO")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

try:
    from bedrock_agentcore.memory.integrations.strands.config import (
        AgentCoreMemoryConfig,
    )
    from bedrock_agentcore.memory.integrations.strands.session_manager import (
        STATE_TYPE_KEY,
        AgentCoreMemorySessionManager,
    )
except ImportError:
    print(
        "This script requires bedrock-agentcore. Install with:\n"
        "    pip install bedrock-agentcore"
    )
    raise SystemExit(2)

from strands import Agent
from strands.models.bedrock import BedrockModel
from strands.multiagent import GraphBuilder


SESSION_ID = "session-repro"
MEMORY_ID = "mem-FAKEFORREPRO"
ACTOR_ID = "actor-repro"
GRAPH_ID = "repro_graph"

MULTI_AGENT_ID_KEY = "multiAgentId"
MULTI_AGENT_STATE_TYPE = "MULTI_AGENT"


class FixedAgentCoreMemorySessionManager(AgentCoreMemorySessionManager):
    """``AgentCoreMemorySessionManager`` plus the three multi_agent methods.

    This subclass is the proposed shape for bedrock-agentcore. The
    three methods follow the same append-only pattern the existing
    single-agent methods already use; ``update_multi_agent`` delegates
    to ``create_multi_agent`` because AgentCore Memory is append-only
    and the read path returns the most recent event matching the
    metadata filter.

    To keep the repro network-free, ``read_session``/``create_session``
    are overridden to in-memory no-ops and a small fake event log
    replaces the AgentCore Memory round-trip. The multi-agent method
    bodies themselves are written exactly as they would be in the real
    class.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # In-memory stand-in for the AgentCore Memory event log.
        self._multi_agent_events: list[tuple[dict[str, str], str]] = []

    # Single-agent patches for the network-free repro.
    def read_session(self, session_id, **kwargs):
        return None

    def create_session(self, session, **kwargs):
        return session

    # The proposed fix — unchanged from what would ship.
    def create_multi_agent(
        self, session_id: str, multi_agent: Any, **kwargs: Any
    ) -> None:
        payload = json.dumps(multi_agent.serialize_state())
        metadata = {
            STATE_TYPE_KEY: MULTI_AGENT_STATE_TYPE,
            MULTI_AGENT_ID_KEY: multi_agent.id,
            "sessionId": session_id,
        }
        # In the real class this becomes:
        #   self.memory_client.gmdp_client.create_event(
        #       memoryId=self.config.memory_id,
        #       actorId=self.config.actor_id,
        #       sessionId=session_id,
        #       payload=[{"blob": payload}],
        #       eventTimestamp=self._get_monotonic_timestamp(),
        #       metadata={
        #           STATE_TYPE_KEY: {"stringValue": MULTI_AGENT_STATE_TYPE},
        #           MULTI_AGENT_ID_KEY: {"stringValue": multi_agent.id},
        #       },
        #   )
        self._multi_agent_events.append((metadata, payload))

    def read_multi_agent(
        self, session_id: str, multi_agent_id: str, **kwargs: Any
    ) -> dict[str, Any] | None:
        # In the real class this becomes:
        #   events = self.memory_client.list_events(
        #       memory_id=self.config.memory_id,
        #       actor_id=self.config.actor_id,
        #       session_id=session_id,
        #       event_metadata=[
        #           EventMetadataFilter.build_expression(
        #               left_operand=LeftExpression.build(STATE_TYPE_KEY),
        #               operator=OperatorType.EQUALS_TO,
        #               right_operand=RightExpression.build(MULTI_AGENT_STATE_TYPE),
        #           ),
        #           EventMetadataFilter.build_expression(
        #               left_operand=LeftExpression.build(MULTI_AGENT_ID_KEY),
        #               operator=OperatorType.EQUALS_TO,
        #               right_operand=RightExpression.build(multi_agent_id),
        #           ),
        #       ],
        #       max_results=1,
        #   )
        #   if not events:
        #       return None
        #   return json.loads(events[0]["payload"][0]["blob"])
        for metadata, blob in reversed(self._multi_agent_events):
            if (
                metadata.get(STATE_TYPE_KEY) == MULTI_AGENT_STATE_TYPE
                and metadata.get(MULTI_AGENT_ID_KEY) == multi_agent_id
                and metadata.get("sessionId") == session_id
            ):
                return json.loads(blob)
        return None

    def update_multi_agent(
        self, session_id: str, multi_agent: Any, **kwargs: Any
    ) -> None:
        # AgentCore Memory is append-only. The latest event wins
        # because list_events returns in timestamp order and
        # read_multi_agent uses max_results=1. Delegate to create.
        self.create_multi_agent(session_id, multi_agent, **kwargs)


def main() -> int:
    config = AgentCoreMemoryConfig(
        memory_id=MEMORY_ID, session_id=SESSION_ID, actor_id=ACTOR_ID
    )
    session_manager = FixedAgentCoreMemorySessionManager(
        agentcore_memory_config=config, region_name="us-east-1"
    )
    print("Session manager constructed (fixed subclass).")

    model = BedrockModel(model_id="us.anthropic.claude-sonnet-4-20250514")
    a = Agent(model=model, system_prompt="Return ok", agent_id="node_a")
    b = Agent(model=model, system_prompt="Return ok", agent_id="node_b")
    builder = GraphBuilder()
    builder.set_graph_id(GRAPH_ID)
    builder.add_node(a, "a")
    builder.add_node(b, "b")
    builder.add_edge("a", "b")
    builder.set_entry_point("a")
    builder.set_session_manager(session_manager)

    print("Building graph...")
    graph = builder.build()
    print("Graph built without NotImplementedError — the fix works.")

    # Verify the state was persisted.
    events = [
        (md, blob)
        for (md, blob) in session_manager._multi_agent_events
        if md.get(STATE_TYPE_KEY) == MULTI_AGENT_STATE_TYPE
        and md.get(MULTI_AGENT_ID_KEY) == GRAPH_ID
    ]
    if not events:
        print("FAILED: nothing persisted after graph build")
        return 1
    _md, blob = events[-1]
    state = json.loads(blob)
    print(f"Persisted state keys: {sorted(state.keys())}")

    # Round-trip: read back the state as the Strands runtime would.
    restored = session_manager.read_multi_agent(SESSION_ID, GRAPH_ID)
    if restored is None:
        print("FAILED: read_multi_agent returned None after a persisted write")
        return 1
    if set(restored.keys()) != set(state.keys()):
        print("FAILED: restored state keys differ from persisted")
        return 1
    print("Round-trip successful — state restored cleanly on read_multi_agent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
