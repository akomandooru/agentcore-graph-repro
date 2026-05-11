# AgentCore Memory Graph Session: minimal repro + fix

This subproject demonstrates that `AgentCoreMemorySessionManager`
cannot back a Strands `Graph` because it does not implement the three
multi-agent methods on Strands' `SessionRepository` interface
(`create_multi_agent`, `read_multi_agent`, `update_multi_agent`).
The methods fall through to the base class and raise
`NotImplementedError`. The repro constructs the real session manager
from `bedrock-agentcore` so the traceback points at the library's
own file.

It also includes a ~50-line fix: three methods added to the session
manager, mirroring the existing single-agent pattern.

## Impact

Any workflow that needs Graph-level session persistence with
AgentCore Memory is blocked. Concretely:

- Classifier-routed multi-agent graphs, including the Strands docs'
  own [Interactive Customer Support](https://strandsagents.com/docs/user-guide/concepts/multi-agent/multi-agent-patterns/)
  canonical Graph example.
- Workflows that use Strands interrupts for human-in-the-loop steps.
  Interrupts require Graph nodes because the Agents-as-Tools pattern
  swallows them.

The single-agent methods (`create_agent`, `read_agent`, `update_agent`)
are fully implemented, so single-agent sessions work fine. The gap is
only in the multi-agent methods, which were added to `SessionRepository`
upstream and default to raising.

## Requirements

- Python 3.10+
- [uv](https://docs.astral.sh/uv/getting-started/installation/)

No AWS credentials are required. The `NotImplementedError` fires at
graph build time, before any AWS API call is made. Fake credentials
are set via env vars so the boto3 client constructor does not fail.

## Setup

Install dependencies into a project-local virtual environment with `uv`:

```bash
uv sync
```

This creates `.venv/` and installs the project in editable mode from
`pyproject.toml`. No activation needed — `uv run` below will use the
right interpreter automatically.

## Run

### Broken

```bash
uv run python repro_broken.py
```

Expected output:

```
Constructing real AgentCoreMemorySessionManager...
Session manager constructed.
Building graph...
FAILED as expected: NotImplementedError: MultiAgent is not implemented for this repository
```

Traceback (trimmed):

```
File ".../strands/multiagent/graph.py", line 390, in build
    return Graph(nodes=self.nodes.copy(), ...)
File ".../strands/multiagent/graph.py", line 476, in __init__
    run_async(lambda: self.hooks.invoke_callbacks_async(MultiAgentInitializedEvent(self)))
File ".../strands/hooks/registry.py", line 306, in invoke_callbacks_async
    callback(event)
File ".../strands/session/session_manager.py", line 54, in <lambda>
    registry.add_callback(MultiAgentInitializedEvent, lambda event: self.initialize_multi_agent(event.source))
File ".../strands/session/repository_session_manager.py", line 332, in initialize_multi_agent
    self.session_repository.create_multi_agent(self.session_id, source, **kwargs)
File ".../strands/session/session_repository.py", line 58, in create_multi_agent
    raise NotImplementedError("MultiAgent is not implemented for this repository")
NotImplementedError: MultiAgent is not implemented for this repository
```

`self.session_repository` is the real `AgentCoreMemorySessionManager`.
The MRO falls through to `SessionRepository` because the AgentCore
class does not override `create_multi_agent`, `read_multi_agent`, or
`update_multi_agent`.

### Fixed

```bash
uv run python repro_fixed.py
```

Expected output:

```
Session manager constructed (fixed subclass).
Building graph...
Graph built without NotImplementedError — the fix works.
Persisted state keys: ['_internal_state', 'completed_nodes', 'current_task',
 'execution_order', 'failed_nodes', 'id', 'interrupted_nodes',
 'next_nodes_to_execute', 'node_results', 'status', 'type']
Round-trip successful — state restored cleanly on read_multi_agent.
```

The fixed script subclasses `AgentCoreMemorySessionManager`, adds the
three methods, and demonstrates that graph state serialises and
restores cleanly.

## What would change in bedrock-agentcore

Three methods added to `AgentCoreMemorySessionManager`, mirroring the
existing single-agent pattern. See `repro_fixed.py` for the full shape;
the method bodies include commented-out pseudo-code showing the
`create_event` and `list_events` calls that would replace the
in-memory event log in a real implementation.

Additions beyond the method bodies:

1. A new `stateType` value: `"MULTI_AGENT"` (alongside the existing
   `"SESSION"` and `"AGENT"`).
2. A new metadata key `multiAgentId` (alongside the existing
   `agentId`) so multiple graphs in the same session do not collide.

Payload shape: `json.dumps(multi_agent.serialize_state())`. The
`update_multi_agent` method delegates to `create_multi_agent` because
AgentCore Memory is append-only and `list_events(max_results=1)`
returns the latest by timestamp, same pattern as `update_agent`.
