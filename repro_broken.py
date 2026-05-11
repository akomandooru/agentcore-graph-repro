"""Demonstrate the bug against the real AgentCoreMemorySessionManager.

This script constructs the actual bedrock-agentcore
``AgentCoreMemorySessionManager`` (not a fake) and demonstrates that
the ``NotImplementedError`` fires before any AWS call is made, because
the multi-agent methods are inherited from Strands'
``SessionRepository`` base and never overridden.

Requires:
    pip install bedrock-agentcore strands-agents

Run:
    python repro_broken.py

No AWS credentials are required for the error to fire — the error is
in the inheritance chain, not in the service logic. We provide fake
boto3 credentials via env vars so the boto3 client constructor does
not fail.

Expected output:
    Constructing real AgentCoreMemorySessionManager...
    Session manager constructed.
    Building graph...
    FAILED as expected: NotImplementedError: MultiAgent is not implemented for this repository
    Traceback points at:
      bedrock_agentcore/memory/integrations/strands/session_manager.py
      <--- the real class has no create_multi_agent/read_multi_agent/update_multi_agent
"""

from __future__ import annotations

import os
import traceback

# Provide placeholder AWS credentials so the boto3 client constructor
# does not fail when no real credentials are configured. setdefault()
# leaves any existing environment values untouched. The repro errors
# before any AWS API call is made, so these values are never used.
os.environ.setdefault("AWS_ACCESS_KEY_ID", "FAKE-FOR-REPRO")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "FAKE-FOR-REPRO")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

try:
    from bedrock_agentcore.memory.integrations.strands.config import (
        AgentCoreMemoryConfig,
    )
    from bedrock_agentcore.memory.integrations.strands.session_manager import (
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


def _patch_read_session(session_manager):
    """Prevent the constructor from hitting AWS on read_session.

    ``RepositorySessionManager.__init__`` (the parent of
    ``AgentCoreMemorySessionManager``) calls
    ``self.read_session(session_id)`` during construction. That would
    normally reach AWS. We patch the instance method to return None so
    the constructor treats it as a fresh session and calls
    ``create_session`` instead — which we also patch to a no-op so no
    outbound call is made.

    The ``NotImplementedError`` we want to demonstrate fires later,
    on graph build, and is unaffected by these patches.
    """
    session_manager.read_session = lambda session_id, **kwargs: None  # type: ignore[method-assign]
    session_manager.create_session = lambda session, **kwargs: session  # type: ignore[method-assign]


def main() -> int:
    print("Constructing real AgentCoreMemorySessionManager...")
    config = AgentCoreMemoryConfig(
        memory_id=MEMORY_ID, session_id=SESSION_ID, actor_id=ACTOR_ID
    )

    # The construction path ordinarily calls read_session on AWS. We
    # patch it below via __init_subclass__-style late binding: subclass
    # that overrides read_session/create_session to in-memory no-ops.
    class LocalOnlyAgentCoreSessionManager(AgentCoreMemorySessionManager):
        def read_session(self, session_id, **kwargs):
            return None

        def create_session(self, session, **kwargs):
            return session

    session_manager = LocalOnlyAgentCoreSessionManager(
        agentcore_memory_config=config, region_name="us-east-1"
    )
    print("Session manager constructed.")

    print("Building graph...")
    model = BedrockModel(model_id="us.anthropic.claude-sonnet-4-20250514")
    a = Agent(model=model, system_prompt="Return ok", agent_id="node_a")
    b = Agent(model=model, system_prompt="Return ok", agent_id="node_b")
    builder = GraphBuilder()
    builder.set_graph_id("repro_graph")
    builder.add_node(a, "a")
    builder.add_node(b, "b")
    builder.add_edge("a", "b")
    builder.set_entry_point("a")
    builder.set_session_manager(session_manager)

    try:
        builder.build()
    except NotImplementedError as e:
        print(f"FAILED as expected: {type(e).__name__}: {e}")
        print()
        print("Traceback:")
        traceback.print_exc()
        print()
        print(
            "The traceback terminates inside "
            "strands/session/session_repository.py because the "
            "AgentCoreMemorySessionManager class in "
            "bedrock_agentcore/memory/integrations/strands/session_manager.py "
            "does not override create_multi_agent, read_multi_agent, or "
            "update_multi_agent."
        )
        return 0
    except Exception as e:
        print(f"UNEXPECTED failure: {type(e).__name__}: {e}")
        traceback.print_exc()
        return 1

    print("UNEXPECTED: graph built without hitting the multi_agent gap")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
