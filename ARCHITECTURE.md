# ARCHITECTURE

A walkthrough of the system in the order a request actually flows. Each
section names the file that owns the behavior. `README.md` covers setup and
running the demo — this document covers *why it is built this way*.

The orchestration layer is built on the OpenAI Agents SDK; the sections below
name the SDK primitive that replaced each hand-rolled component of the
previous architecture (router, planner, prompt builder, ReAct loop, model
adapters, MCP client adapter, session store).

---

## 1. The system in one paragraph

An autonomous coding agent as a small, auditable pipeline: a FastAPI gateway
accepts a natural-language message plus a target repository, and one SDK
`Agent` — driven by `Runner.run_streamed()` — decides on its own when to call
a tool and when to answer in text. The agent can call a strictly whitelisted
set of filesystem tools and MCP external tools. Every action is observed and
recorded, and — after file mutations — checked by the repository's own test
command. The final summary is returned as JSON, and streamed runs observe
each action live. Conversation is one session per client run: the REPL reuses
a single session id, and follow-up messages reach the model with that
context.

```
client (terminal / interactive REPL)
        |   POST /v1/agent/run (or /run/stream for live events)
        v
FastAPI gateway (app/main.py, app/api/routes.py)      -- AgentRequest
        v
AgentRunner (app/agent.py)                            -- per-request preparation:
        |     repository analysis (app/intelligence/)
        |     MCP server connections (bounded, failure-isolated)
        |     BoundedSession over SQLiteSession
        v
SDK Agent (instructions + tools + model)
        |-- LitellmModel: anthropic/<name> or deepseek/<name>
        |-- @function_tool filesystem tools (app/tools/filesystem.py)
        |-- run_tests tool (app/testing/runner.py)
        v
tool calls -> observations -> ... -> final text       -- bounded by max_turns
```

The target repository is always supplied per request; the agent service's own
repository is never assumed to be the target.

## 2. Gateway (`app/main.py`, `app/api/routes.py`)

`create_app()` wires an `AgentRunner` built from environment settings.
Routes expose `POST /v1/agent/run` (JSON response) and `POST /v1/agent/run`
`/run/stream` (server-sent events). Both transports share one event pipeline:
the run is always streamed, and the non-streaming endpoint simply ignores the
progress events. Error mapping: configuration problems (`ValueError`, e.g. a
missing API key) become HTTP 400; provider and SDK failures become
HTTP 502 (`RuntimeError`).

The request/response contract (`AgentRequest` / `AgentResponse` in
`app/contracts.py`) is unchanged from the previous architecture: task,
target_repo, apply_changes, session_id in; plan, status, observations,
summary, session_id out.

## 3. The agent (`app/agent.py`)

One module owns everything the SDK needs per request:

- **Model**: `build_model()` maps `AGENT_MODEL_PROVIDER` to
  `LitellmModel(model="anthropic/<name>")` or
  `LitellmModel(model="deepseek/<name>", base_url=...)`. The API key is
  checked before any error handling, so configuration problems surface as
  HTTP 400 instead of being masked downstream.
- **Tools**: `filesystem_tools()` wraps the existing `FilesystemTool` as
  `@function_tool` functions (`fs_list`, `fs_read`, plus `fs_create`,
  `fs_write`, `fs_edit` when authorized) and `test_tool()` exposes the
  repository's configured test command as `run_tests`. Each wrapper returns a
  JSON envelope that decodes back into the auditable contract records.
- **Repeat guard**: `_apply_repeat_guard()` wraps every tool invocation —
  filesystem, test, and MCP alike — and blocks an action identical to the
  previous one with an auditable failed observation. The SDK has no built-in
  equivalent; the guard state lives in the per-run context.
- **MCP**: `BoundedMCPServer` delegates to `MCPServerStdio` /
  `MCPServerStreamableHttp` and caps every advertisement (8 tools, 300-char
  descriptions, 2 KB schemas — oversized schemas become a bounded placeholder
  that keeps the tool callable). Server-flagged tool errors are re-wrapped as
  a JSON marker so they surface as failed observations. One failed server
  never blocks the others: connection failures are collected and reported in
  the agent's instructions.
- **Session**: `BoundedSession` extends the SDK `Session` over
  `SQLiteSession` (`AGENT_SESSION_DB`), clipping stored user/assistant text
  and replaying only a bounded window so a long REPL session cannot grow the
  prompt without bound.
- **Run**: `Runner.run_streamed(..., max_turns=AGENT_MAX_ITERATIONS)` drives
  the loop. `MaxTurnsExceeded` becomes the FAILED
  "Stopped after reaching the N-action limit." response. Tool-call and
  tool-output item events are decoded into `plan` / `action` / `observation`
  progress events, so both endpoints observe the run live.

## 4. Filesystem tools (`app/tools/filesystem.py`, unchanged)

Five operations only: list, read, create, write, edit. Deletion and shell
execution deliberately do not exist, so no permission check is needed to
reject them — the model cannot ask for what is not implemented.

- **Path confinement**: every requested path is resolved against the fixed
  target root (`resolve(strict=False)` then `relative_to`), re-validated on
  every call. `resolve()` collapses `..` segments and follows symlinks, and
  the `relative_to` check rejects anything that lands outside the root —
  including symlink escapes created by earlier agent writes.
- **Change authorization is structural**: `FilesystemTool` keeps its own
  `allow_changes` check (defense in depth), but the primary guardrail is in
  `app/agent.py`: when a request has `apply_changes=False` the mutating tool
  functions are never constructed, so they are never advertised to the model.
- **Edit exactness**: `edit` replaces exactly one verbatim occurrence of
  `old_text`; zero or multiple occurrences fail loudly.
- **Auditable results**: operations return success/failure records instead of
  raising; one bad tool call can never crash the request.

## 5. Test execution (`app/testing/runner.py`, unchanged)

The only shell-free subprocess in the system. A repository opts in with a
`[test]` section in its own `.coding-agent.toml` containing an argument list
and optional timeout; model text is never turned into a command. After every
successful mutation the configured suite runs automatically, and `run_tests`
is additionally advertised as a tool for authorized runs.

## 6. Repository intelligence (`app/intelligence/`, unchanged)

A stdlib-`ast` indexer (`python_analyzer.py`) builds a read-only structure
index per target repository: every Python file with module name, top-level
symbols, and imports, plus the internal import graph and recoverable parse
issues. `summary.py` renders a bounded (2 KB) summary that is embedded in the
per-request context, so the agent reasons about repo structure instead of
guessing from file names. One broken file never blocks a request.

## 7. Safety-relevant invariants (where each lives)

| Invariant | Enforcement |
|---|---|
| Path confinement | `FilesystemTool._resolve_path` — re-checked per call |
| Structural change authorization | `filesystem_tools()` never builds mutating tools without `apply_changes`; `FilesystemTool._check_permission` as defense in depth |
| Edit exactness | `FilesystemTool._edit` — exactly-one-occurrence rule |
| No shell, no delete | No such tool exists; tests run via `TestRunner` with `shell=False` from repo-owned config only |
| Auditable results | Every tool output decodes to `ToolResult` / `TestResult` / `ExternalToolResult` with error text on failure |
| Bounded action budget | `max_turns` on `Runner.run_streamed`; `MaxTurnsExceeded` → FAILED response |
| Bounded external tools | `BoundedMCPServer.list_tools` caps count, description length, and schema size |
| Bounded memory | `BoundedSession` clips stored text and replays a bounded window |

## 8. What the SDK replaced

| Deleted | Replaced by |
|---|---|
| `app/models/` (provider contract + Anthropic/DeepSeek adapters + router) | `LitellmModel` in `app/agent.py` |
| `app/mcp/` (client adapter + connection manager) | `MCPServerStdio` / `MCPServerStreamableHttp` behind `BoundedMCPServer` |
| `app/orchestrator/graph.py` (ReAct loop) | `Runner.run_streamed()` with `max_turns` |
| `app/prompts/builder.py` | `Agent(instructions=...)` + `@function_tool` schemas |
| `app/router/service.py`, `app/planner/service.py` | The agent's native tool-vs-text behavior |
| `app/memory/session.py`, `app/memory/context.py` | `BoundedSession` over `SQLiteSession` |
