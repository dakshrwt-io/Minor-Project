# ARCHITECTURE

A walkthrough of the system in the order a request actually flows. Written so
every component can be explained without reading its source first; each
section names the file that owns the behavior. `README.md` covers setup and
running the demo — this document covers *why it is built this way*.

---

## 1. The system in one paragraph

An autonomous coding agent as a small, auditable pipeline: a FastAPI gateway
accepts a natural-language message plus a target repository, a triage router
classifies the message — conversation is answered directly, a coding task is
handed to a planner that turns it into an ordered plan — and a bounded ReAct
loop (Plan → Act → Observe, capped by an iteration limit) drives a language
model that can call a strictly whitelisted set of filesystem tools and MCP
external tools. Every action is observed, recorded, and — after file
mutations — checked by the repository's own test command. The model sees the
accumulated observations on every turn and eventually replies with a final
summary, which the gateway returns as JSON. Within one client run the
conversation is one session: the REPL reuses a single session id, a bounded
volatile store keeps the prior turns, and follow-up messages reach the
router, planner, and loop with that context.

```
client (terminal / interactive REPL)
        │  POST /v1/agent/run (or /v1/agent/run/stream for live events)
        ▼
FastAPI gateway (app/main.py, app/api/routes.py)
        │  AgentRequest
        ▼
ReActOrchestrator (app/orchestrator/graph.py)  ── per-request preparation:
        │      repository analysis (app/intelligence/),
        │      triage: chat or task? (app/router/service.py)
        │           ├─ chat → COMPLETED (the reply is the summary)
        │      MCP connections (app/mcp/)
        ▼
ReAct loop:  plan → act ⇄ observe → limit → END
        │            │       │
        │            │       └─ tools: filesystem (app/tools/filesystem.py,
        │            │          structural change guardrail), tests
        │            │          (app/testing/runner.py), MCP (app/mcp/)
        │            └─ prompt: app/prompts/builder.py
        │               model: app/models/router.py → provider adapter
        └─ planner: app/planner/service.py
        │
        ▼
AgentResponse (plan, status, observations, summary) → client
```

---

## 2. Request lifecycle, file by file

### 2.1 Client layer — `client/`

- **`client/__main__.py`** — makes `python -m client` work; delegates to
  `client.terminal.main`.
- **`client/terminal.py`** — single-shot CLI. Parses `--task`, `--target-repo`,
  `--apply-changes`, `--interactive`, `--base-url`, `--no-stream`, `--timeout`;
  builds the JSON body (`build_payload`), and renders the transcript (`render`).
  By default it consumes the streaming endpoint (`stream_request`): each
  server-sent event is rendered by `render_event` the moment it arrives, so
  plan, actions, and observations appear live. If the gateway has no streaming
  endpoint (404) it falls back to the plain `post_request` POST; `--no-stream`
  forces that mode directly. Exit code: `0` on `status == "completed"`, `1`
  otherwise, `2` on usage or connection errors.
- **`client/interactive.py`** — REPL variant (`--interactive`). Same payload
  and transport; adds slash commands (`/apply`, `/repo`, `/base-url`,
  `/clear`, `/help`, `/quit`), Rich panels, and colored observation lines
  printed live during the run (the final panel then omits the already-shown
  observations; the pre-streaming POST path is kept as fallback).
- **`client/formatting.py`** — the single source of truth for "what does one
  observation or one stream event look like as a line of text" on the client
  side: `classify_observation` detects the kind by payload key (`call` →
  filesystem, `command` → test, neither → external), `excerpt` truncates
  detail to 240 characters, `describe_observation` renders the plain-text
  line, and `describe_action` renders one live `action` event. Both clients
  render from these shared facts; the interactive client only adds color and
  markup escaping. Standard library only, so the single-shot client needs no
  Rich.

### 2.2 Gateway — `app/main.py`, `app/config.py`, `app/api/routes.py`

- **`app/main.py`** — application factory. `build_default_orchestrator` wires
  production dependencies from `Settings`: model router, planner, prompt
  builder, iteration cap, MCP server list. `create_app`
  accepts an orchestrator override so tests can inject a fake model — the
  reason the whole stack is testable without API keys.
- **`app/config.py`** — environment-backed `Settings` (frozen dataclass).
  Reads `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, `AGENT_MODEL_PROVIDER`,
  `AGENT_MODEL`, `AGENT_MODEL_BASE_URL`, `AGENT_MAX_ITERATIONS`,
  `AGENT_MCP_SERVERS`. A repository-root `.env`
  file is loaded first; real environment variables always win over it.
- **`app/api/routes.py`** — two endpoints on one orchestrator boundary:
  `POST /v1/agent/run` returns the finished `AgentResponse` as JSON, and
  `POST /v1/agent/run/stream` streams the same run as server-sent events
  (`plan`, `action`, `observation`, then a terminal `done` event carrying the
  full response — or an `error` event carrying the HTTP-equivalent code).
  Streaming is implemented by handing the orchestrator an `emit` callback that
  pushes events into an asyncio queue drained by a `StreamingResponse`
  generator; cancellation on client disconnect cancels the run task. Both
  routes map errors the same way: `ValueError` → 400 (bad
  input/configuration), `RuntimeError` → 502 (model or gateway failure),
  otherwise 200. Neither route contains business logic — they are boundaries
  only.

### 2.3 Orchestrator preparation — `app/orchestrator/graph.py` (`run`)

Before the loop starts, `ReActOrchestrator.run` (all blocking work dispatched
to worker threads via `asyncio.to_thread`):

1. Resolves `request.target_repo` to an absolute path (the fixed root for
   everything that follows).
2. Resolves the session id: the request's when the client supplies one (the
   interactive REPL sends the same id for every message of its run), a fresh
   UUID otherwise — single-shot requests stay one-message-per-session.
3. Analyzes and summarizes the repository (see 2.4).
4. Loads the session's prior conversation turns from the volatile session
   store, bounded (see 2.6).
5. Runs the triage router (see 2.5). A `chat` decision ends here: the
   response carries a zero-step plan, the router's reply as summary, and
   status `completed` — a `plan` event is still emitted so streaming
   clients render it like any other run. A `task` decision continues.
6. Constructs the request's `FilesystemTool` with `allow_changes` taken from
   the request — authorization is per request, not global.
7. Opens all configured MCP servers for the duration of the request
   (`open_mcp_servers`), discovers their tools, and builds the
   server-qualified tool map.
8. Enters the Plan → Act → Observe loop (see 2.7).
9. Records the finished turn (message, reply, route) in the session store so
   later messages in the same session can refer back to it.

### 2.4 Codebase intelligence — `app/intelligence/`

- **`python_analyzer.py`** — walks the target root (skipping `.git`,
  `__pycache__`, `.venv`, etc.), parses every `.py` file with the stdlib
  `ast` module, and produces a `PythonProjectIndex`: per-file module names,
  top-level symbols, and import references, plus a module-to-module
  relationship graph built by resolving imports to fully-qualified names
  (relative imports resolved by walking up package parts — worked example in
  the `_import_targets` docstring). Parse errors become `ParseIssue` records
  instead of failing the analysis: one broken file never blocks a request.
  No external indexer; deliberately dependency-light.
- **`summary.py`** — renders the index into a bounded text summary (hard caps
  on modules, symbols, relationships, and total characters). Deterministic:
  same repository, same summary, every time — which keeps prompts
  reproducible. This summary is what the model sees, so it reasons about repo
  structure instead of guessing from file names.

### 2.5 Triage router — `app/router/service.py`

Runs once, before the planner, and classifies the user message:
conversation or coding task. Conversation (greetings, small talk, thanks,
simple questions about the repository) is answered directly — the
orchestrator short-circuits with a zero-step plan and the reply becomes the
response summary; no planner call, no loop, no filesystem access. Any
actionable coding request is handed off to the pipeline below unchanged.

The router exists because a planner forced to produce steps will fabricate
work from a plain "hello" — the cheapest escape from "you must plan" is
inventing a task. Triage before planning removes that escape structurally
rather than by prompt exhortation, and mirrors the main-agent-then-handoff
shape of production coding agents.

`ModelTaskRouter` classifies with one model call via a forced `route_reply`
tool (`route: "chat" | "task"`, optional `reply`), validated all-or-nothing:
a chat decision must carry a non-blank reply, a task decision must carry
none, and any malformed output falls back to `DeterministicTaskRouter`,
which routes everything to the planner — exactly the pre-router behavior, so
the fallback never misroutes and keyless scripted demos stay deterministic.
The router sees the session's bounded prior turns (see 2.6), so follow-up
messages are classified with their context instead of in isolation. The
router never executes tools; its only output is a `RouteDecision`.

### 2.6 Memory — `app/memory/`

Conversation and observations are deliberately volatile: nothing is
persisted, and everything lives in RAM until its process ends.

- **`session.py`** — `SessionStore`, the cross-request conversation memory.
  The client owns session identity: the interactive REPL mints one UUID at
  startup and sends it with every message, so its turns accumulate under one
  session id; requests without an id get a freshly minted one. Each recorded
  turn (user message, agent reply, route) is whitespace-normalized and
  clipped (800 chars per side), sessions keep at most 12 turns, and the
  store remembers at most 512 sessions with oldest-first eviction — bounded
  like everything else. The store dies with the gateway process, so
  restarting either the client or the gateway is a new session by
  construction. `payload()` renders the most recent turns as JSON-serializable
  context that the orchestrator passes — byte-identically — to the router,
  the planner, and every loop prompt, so follow-ups like "what is my name"
  or "now add tests for it" resolve against the real antecedent.
- **`context.py`** — `ObservationCompactor`, called by the prompt builder on
  every `act` iteration. Keeps the most recent 4 observations verbatim (the
  model needs exact recent tool output) and collapses older ones into a
  one-line-each summary with a 1,500-character cap. Deterministic — no model
  call, no embeddings. This is the within-run memory: it lives in RAM and
  dies with the run.

### 2.7 The ReAct loop — `app/orchestrator/graph.py` (`ReActOrchestrator.run`)

One plain async `while` loop — no graph framework. Control flow:

```
loop: build prompt → model ──(tool call)──→ execute → observe ──(budget remains)──→ loop
                     └─(plain text)→ COMPLETED      └─(budget gone)→ FAILED (limit)
```

- **Plan (runs once, before the loop)** — calls the task planner; the
  resulting `TaskPlan` is fixed for the rest of the run. Only observations
  grow afterwards.
- **Act (the model turn)** — the prompt builder assembles a complete
  stateless prompt (see 2.8) and the model client executes it. If the model
  returned a tool call, the first one is executed below; if it returned plain
  text, that text becomes the final summary and the loop ends with
  `status=completed`.
- **Repeat guard** — a model-issued call identical (tool name + arguments) to
  the immediately preceding one is not executed: it becomes a failed
  observation telling the model to vary its action or finish in text. Without
  this, a looping model burns its whole budget re-listing the same directory.
- **Observe (the action turn)** — executes the pending call and appends the
  result to the observation log. Filesystem calls go to the filesystem tool
  (in a worker thread); external calls go through the MCP adapter, where an
  unadvertised tool name is rejected with an auditable failure result rather
  than crashing. After a *successful* create/write/edit, the target
  repository's own test command runs immediately, so the model sees the
  pass/fail outcome on its next turn — this is what lets the agent notice
  breakage and self-correct.
- **Emit (live progress)** — with every plan/action/observation the loop
  awaits an optional `emit` callback (an `asyncio.Queue` push). The streaming
  route consumes that queue; `orchestrator.run` is otherwise unchanged.
- **Limit (exit guard)** — if the model kept requesting actions until the
  budget (`AGENT_MAX_ITERATIONS`, default 6) ran out, the run ends with
  `status=failed` and an explicit "reached the N-action limit" summary.
  Failing explicitly beats looping forever. The model sees its own
  `action_budget` (limit/used/remaining) in every prompt so it can pace
  itself.

State is a handful of local variables (`plan`, `observations`, `iterations`)
— the prompt builder serializes everything the model needs on every turn, so
no separate state object exists.

### 2.8 Prompt construction — `app/prompts/builder.py`

The **only** place where agent state becomes model-visible text. On every
iteration it rebuilds the prompt from scratch:

- a system prompt distilled from how production coding agents (Claude Code,
  opencode) discipline their loops: act when ready instead of surveying the
  repository, never repeat an identical action, treat every call as budgeted,
  make minimal changes, and stop calling tools — reply with plain text — as
  soon as the task is satisfied. It also states the boundaries the tools
  already enforce (target_root only; no shell, deletion, or network exist);
  when a task needs a missing capability the model is told to explain that in
  its final text instead of thrashing;
- the tool list: `fs_list`/`fs_read` always; `fs_create`/`fs_write`/`fs_edit`
  **only when the request authorized changes** — the guardrail is structural
  (the tools simply are not advertised), not a plea in the prompt;
- external MCP tools advertised as ordinary native tools, with bounded
  counts/description lengths/schema sizes (they come from outside this
  codebase and would otherwise be an unbounded prompt-size and injection
  surface);
- one JSON user message containing the full state snapshot: target root,
  plan (including the planner's `relevant_files` grounding), authorization,
  the action budget, repository summary, the compacted observation context,
  and any MCP startup errors.

The conversation is deliberately stateless: instead of accumulating message
history, every prompt is a complete snapshot. That makes each model call
auditable (log it and you know exactly what the model saw), prevents
unbounded context growth, and behaves identically across providers.

### 2.9 Model layer — `app/models/`

- **`base.py`** — the provider-neutral contract: `ModelRequest` (system
  prompt, messages, tool specs, `max_tokens` — 8,192 by default, because
  reasoning-style providers spend the same budget on hidden reasoning before
  any visible output, and a small cap comes back as an empty body),
  `ModelResponse` (normalized text + tool calls), and the `ModelClient` ABC.
  Application code never imports a provider SDK.
- **`router.py`** — maps `AGENT_MODEL_PROVIDER` (`anthropic` | `deepseek`) to
  a factory and returns the configured client. A second provider is one
  factory entry.
- **`anthropic.py`, `deepseek.py`** — thin adapters that translate the
  neutral request to the provider API and normalize the reply (text, tool
  calls, model name) back into `ModelResponse`.

### 2.10 Planner — `app/planner/service.py`

Runs once, before the loop. `ModelTaskPlanner` makes one model call with a
forced `submit_plan` tool (structured output instead of free text) and plans
**from evidence**: the prompt hands the model the task plus the AST-derived
`repository_summary` and requires it to derive `relevant_files` (exact paths
the task touches) and to write step descriptions that name real files or
symbols rather than vague wording — the executing agent has no other
knowledge of the codebase, so the plan is its map. The planner validates the
arguments all-or-nothing (any malformed step or malformed `relevant_files`
list rejects the whole plan) and falls back to `DeterministicTaskPlanner` —
a fixed inspect → decide → complete plan used for keyless demos. Config
errors (e.g. missing API key) propagate; only unusable plan *content* falls
back. The plan's final step reflects `apply_changes`, so the plan matches
what the agent may actually do.

### 2.11 Tools — `app/tools/`

- **`app/tools/filesystem.py`** — a capability whitelist plus a path jail.
  Five operations (`list`, `read`, `create`, `write`, `edit`) and nothing
  else: deletion and shell execution do not exist to be refused. Every
  model-supplied path is re-resolved and re-validated per call:
  `resolve(strict=False)` collapses `..` and follows symlinks, then
  `relative_to` rejects anything outside the target root. Failures return a
  `ToolResult` with an error message — never an exception — so the loop can
  observe and retry. A `_check_permission` method evaluates each call before
  execution: read-only operations always allowed; create/write/edit require
  `apply_changes=true`, with a reason string for the audit trail. (The
  prompt builder hides mutation tools too — the check is the second,
  independent layer.)

### 2.12 Test runner — `app/testing/runner.py`

Executes the target repository's *own* test command, which the repository
must opt into via a `.coding-agent.toml` file:

```toml
[test]
command = ["python", "-m", "pytest", "-q"]
timeout_seconds = 60
```

The command is an explicit argument list run with `shell=False` from the
target root — the agent never turns model text into a shell command. Timeouts
and missing executables become failed `TestResult`s. No config file means no
tests run (the feature is opt-in per repository).

### 2.13 MCP — `app/mcp/`

- **`connection.py`** — parses `AGENT_MCP_SERVERS` (a JSON list of
  `{name, command, args}` stdio servers), opens every configured server when
  a request starts, and closes them when it ends. One failed server never
  blocks the others; its error is reported to the model alongside the live
  connections. The official MCP SDK is imported lazily, so non-MCP runs never
  require it.
- **`adapter.py`** — adapts an MCP session to the internal tool contract.
  Tool names are **server-qualified** (`docs.search_docs`): qualification
  makes cross-server name collisions impossible and lets the orchestrator
  route a model-issued call back to the owning session, where the adapter
  strips the prefix before calling the server.

### 2.14 Response — back through `graph.py` and `routes.py`

When the loop ends, the orchestrator assembles `AgentResponse` (session id,
plan, final status, the full observation log, the model's summary) and the
gateway serializes it as JSON.
The MCP context manager closes all server sessions.

---

## 3. The contract types — `app/contracts.py`

All cross-boundary data shapes: `AgentRequest`/`AgentResponse` (gateway;
`AgentRequest.session_id` is the client-owned conversation-memory key),
`TaskPlan`/`TaskStep` (planner), `RouteDecision` (router),
`ToolCall`/`ToolResult` (filesystem), `TestResult`,
`ExternalToolCall`/`ExternalToolResult` (MCP), plus the `TaskStatus` and
`FilesystemOperation` enums. Results carry validated invariants (e.g. success
implies no error) enforced by pydantic validators.

**Modeling style rule — pydantic vs dataclass:** pydantic for anything that
crosses a trust or serialization boundary (untrusted input to validate, or
data that must serialize for the API or prompts); plain frozen dataclasses
for trusted internal value records (`PermissionDecision`, `TestCommand`,
`TestRunResult`, MCP config/connection, intelligence records). Both are
immutable; the split is about where validation and serialization pay off.

---

## 4. Design decisions worth defending

| Decision | Why |
|---|---|
| Mutations require `apply_changes: true` | Read-only by default; the demo can inspect safely and only writes when explicitly authorized. |
| Structural guardrail (hidden tools) + policy check | Two independent layers: the model cannot call a tool it was never shown, and even a smuggled call is refused at execution. |
| Per-call path re-validation | The path arrives fresh from the model every call; re-resolving catches `..` traversal and symlink escapes, including ones created by earlier agent writes in the same session. |
| No deletion / no shell, ever | The strongest boundary is a capability that does not exist. The test command is the only subprocess, and it comes from a repo-owned config file, never from model text. |
| Stateless prompts (full snapshot per iteration) | Auditability (log = exact model input), bounded context, provider-neutral. |
| Repeat-action guard at the loop, not the prompt | A model that repeats an identical call is stopped structurally and told why; discipline instructions alone do not bound a looping model. |
| Triage router before the planner | A planner forced to produce steps will fabricate work from "hello"; classification removes that escape structurally. Conversation never reaches the filesystem, and chat replies skip two model stages. The fallback routes everything to the planner, so triage can only add behavior, never silently swallow a task. |
| Client-owned session id, server-side bounded history | A REPL conversation is one session — follow-ups reference real prior turns — while single-shot requests and restarts stay isolated by construction. Memory is bounded (12 turns, 512 sessions, clipped text) and volatile (dies with the gateway), so continuity cannot grow into unbounded state or leak across processes. |
| Server-qualified MCP tool names | Collision-free across servers; routing a call to its owning session is string-prefix bookkeeping. |
| Failures as observed results, not exceptions | The ReAct loop learns from failures; one bad call must never end the request. |
| Deterministic compaction and summaries | Same inputs → byte-identical prompts → reproducible demos and tests. |
| Everything bounded (iterations, tool counts, schema sizes, summary lengths) | The model and external servers are not fully trusted; no single actor can inflate cost or context without limit. |

## 5. Deliberately out of scope

Parallel tool calls, git operations, file deletion,
shell execution, token-level streaming of model output (actions and
observations stream; model text does not), vector-memory (session continuity
uses compacted text summaries instead), and an IDE extension. Each is an
isolated extension
point: a new tool is one class + one prompt-builder entry; a new provider is
one router factory; a new client is anything that can POST JSON.
