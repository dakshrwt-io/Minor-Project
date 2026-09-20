"""The coding agent built on the OpenAI Agents SDK.

A single `Agent` driven by `Runner.run_streamed()` replaces the hand-rolled
router, planner, prompt builder, ReAct loop, model router, and MCP client
adapter:

- `LitellmModel` (selected by AGENT_MODEL_PROVIDER) replaces app/models/.
- `MCPServerStdio` / `MCPServerStreamableHttp` behind `BoundedMCPServer`
  replace app/mcp/.
- `@function_tool` wrappers over `FilesystemTool` / `TestRunner` replace
  app/prompts/builder.py and the loop's tool dispatch table.
- `BoundedSession` (an SDK `Session` over SQLiteSession) replaces app/memory/.
- The streamed run's item events replace the notify() callback.

Safety guarantees are structural, not prompt instructions:

- Path confinement lives in FilesystemTool and is re-checked on every call.
- When `apply_changes=False` the mutating tools are never built, so the model
  is never advertised a mutating capability.
- The bounded action budget maps to `max_turns`; `MaxTurnsExceeded` becomes
  the FAILED "hit the limit" response.
- External MCP tool advertisements are bounded in tool count, description
  length, and schema size before they reach the model.
- Repeated identical actions are blocked between turns (the SDK has no
  built-in equivalent), and every tool failure is rendered as an auditable
  model-visible result.
"""

from app.agent.mcp import _MAX_EXTERNAL_TOOLS, BoundedMCPServer, build_mcp_server
from app.agent.observations import _decode_tool_output
from app.agent.prompts import SYSTEM_INSTRUCTIONS_TEMPLATE, build_model
from app.agent.runner import (
    AgentRunner,
    GuardedAgent,
    ProgressEmitter,
    RunArtifacts,
    _apply_repeat_guard,
    build_agent,
)
from app.agent.session import BoundedSession
from app.agent.tools import filesystem_tools, test_tool

__all__ = [
    "AgentRunner",
    "BoundedMCPServer",
    "BoundedSession",
    "GuardedAgent",
    "ProgressEmitter",
    "RunArtifacts",
    "SYSTEM_INSTRUCTIONS_TEMPLATE",
    "_MAX_EXTERNAL_TOOLS",
    "_apply_repeat_guard",
    "_decode_tool_output",
    "build_agent",
    "build_mcp_server",
    "build_model",
    "filesystem_tools",
    "test_tool",
]
