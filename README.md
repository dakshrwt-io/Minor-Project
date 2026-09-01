# Autonomous Coding Agent

A demo-oriented autonomous coding agent for a college minor project. It accepts a natural-language task and an explicit target repository, plans a bounded sequence of actions, uses guarded filesystem tools, and returns an auditable result through FastAPI.

> Follow [`DEMO.md`](DEMO.md) for a ~15 minute scripted walkthrough, including a keyless track that needs no API key.

## Current capabilities

- FastAPI gateway: `POST /v1/agent/run` and `POST /v1/agent/run/stream` (server-sent live events)
- Triage router: conversational messages answered directly; coding tasks handed to the planner
- Session continuity in the REPL: one session per client run — follow-up messages reference earlier turns; bounded, volatile server-side memory
- Bounded Plan → Act → Observe loop (plain async loop, no graph framework)
- Anthropic and DeepSeek model adapters behind a provider-neutral model router
- Confined filesystem `list`, `read`, `create`, `write`, and exact-match `edit` actions
- Explicit change authorization: mutations require `apply_changes: true`
- Optional repository-owned test command, run after successful file changes
- Self-contained volatile sessions: a fresh `session_id` per run, no persistence
- Python AST repository context: files, imports, top-level symbols, internal import edges, and parse issues
- Bounded prompt context for observations and repository summaries
- Configured stdio MCP servers: discovery of advertised tool schemas, surfaced as bounded native tool advertisements, with model-issued calls executed against live sessions
- Terminal clients: single-shot CLI and an interactive Rich-based REPL with slash commands

## Architecture

```text
HTTP gateway
  → triage router (chat answered directly, tasks continue)
  → task planner
  → ReAct orchestrator (plain async loop)
      → prompt builder → model router → provider adapter
      → guarded filesystem tool
      → optional target-repository test runner
      → Python AST repository analyzer
      → configured stdio MCP servers (tool-schema advertisement)
```

The target repository is always supplied per request. The agent service repository is never assumed to be the target.

## Setup

Requires Python 3.11 or newer.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Run the automated checks:

```powershell
python -m pytest tests -q -p no:cacheprovider
python -m ruff check app tests
python -m ruff format --check app tests
```

## Configuration

Copy values from `.env.example` into your environment. The application reads environment variables directly and also loads a repository-root `.env` file at startup; real environment variables always win over `.env` values.

```powershell
$env:ANTHROPIC_API_KEY = "your-key"
$env:AGENT_MODEL_PROVIDER = "anthropic"
$env:AGENT_MODEL = "claude-sonnet-4-20250514"
$env:AGENT_MODEL_BASE_URL = "https://proxy.example.com"   # optional; routes provider calls through a custom endpoint
$env:AGENT_MAX_ITERATIONS = "6"
$env:AGENT_MCP_SERVERS = '[{"name":"docs","command":"python","args":["-m","docs_server"]}]'
```

`ANTHROPIC_API_KEY` is required only for live model-backed runs. Unit and integration tests use fake models and require no key. `AGENT_MODEL_BASE_URL` is optional; when set, the Anthropic adapter forwards it to the SDK as `base_url`, which supports proxies and gateway endpoints.

### Providers

- **Anthropic** (default): `AGENT_MODEL_PROVIDER=anthropic`, `ANTHROPIC_API_KEY`, model names like `claude-sonnet-4-20250514`.
- **DeepSeek**: `AGENT_MODEL_PROVIDER=deepseek`, `DEEPSEEK_API_KEY`, `AGENT_MODEL=deepseek-chat` (or `deepseek-reasoner`). The adapter uses DeepSeek's OpenAI-compatible API (`https://api.deepseek.com`) and honors `AGENT_MODEL_BASE_URL` when set.
- DeepSeek also exposes an Anthropic-compatible endpoint: keep the Anthropic provider and set `AGENT_MODEL_BASE_URL=https://api.deepseek.com/anthropic`.

`AGENT_MCP_SERVERS` is an optional JSON list of stdio MCP server entries, each with a `name`, `command`, and string `args`. The agent starts each configured server per request, collects its advertised tool schemas, and advertises them as bounded native tools alongside the filesystem tools. Tool names are server-qualified (`docs.search_docs`), so the model can invoke them; calls are routed back to the owning session, and only advertised names are callable. Startup failures and unknown tool names are reported to the prompt context or observation stream and never abort the request.

## Run the API

From the repository root, either start it directly:

```powershell
python -m uvicorn app.main:app --reload
```

or use the helper, which warns when `ANTHROPIC_API_KEY` is missing in the current terminal:

```powershell
.\start_gateway.ps1
```

The interactive API schema is then available at `http://127.0.0.1:8000/docs`. The root path `/` intentionally returns 404; the agent endpoint is `POST /v1/agent/run`.

> Restart uvicorn after changing environment variables: `--reload` only watches source files, and the worker inherits the launch shell's environment.

## Invoke the agent

Use an existing directory as `target_repo`.

```powershell
$body = @{
  task = "Read the README and summarize the smallest documentation improvement"
  target_repo = "C:\path\to\demo-repository"
  apply_changes = $false
} | ConvertTo-Json

Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/v1/agent/run" `
  -ContentType "application/json" `
  -Body $body
```

Set `apply_changes` to `$true` only when you authorize the agent to create, write, or edit files. The response includes a `session_id`, ordered plan, filesystem and test observations, status, and final summary.

## Terminal client

Two modes, both standard-library-and-Rich only, run against a gateway.

Single-shot:

```powershell
python -m client --task "Read the README and summarize the smallest documentation improvement" `
  --target-repo "C:\path\to\demo-repository"
```

Interactive REPL (Claude-Code style):

```powershell
python -m client --interactive --target-repo "C:\path\to\demo-repository"
```

The REPL keeps the gateway, target repository, and change authorization in a
header, streams each action and observation live as the agent works, and
renders the final result in a colored panel with plan steps, status, session
id, and summary. Slash commands: `/apply`, `/repo <path>`, `/base-url <url>`,
`/clear`, `/help`, `/quit`, `/exit`.

Add `--apply-changes` to authorize file mutations (or `/apply` in the REPL),
`--base-url` to point at a different gateway, and `--timeout` to bound the
request. The single-shot client exits `0` on completed, `1` on agent failed,
`2` on client or gateway error.

### Live progress streaming

By default the client consumes `POST /v1/agent/run/stream` (server-sent
events): the plan, each tool action, and each observation arrive as they
happen and are printed immediately, with a final `done` event carrying the
full response. If the gateway predates the streaming endpoint, the client
falls back to the plain `POST /v1/agent/run` request-and-wait behavior; pass
`--no-stream` to force that mode.

### Conversation, not just tasks

Every message is triaged first (`app/router/service.py`): greetings, small
talk, and simple repository questions are answered directly without touching
the planner, the loop, or the filesystem — a conversational message can
never be turned into an invented coding task. Actionable requests flow to
the planner and ReAct loop unchanged.

The interactive REPL keeps one session for its whole run: every message
carries the same session id, the gateway remembers a bounded number of prior
turns, and follow-ups like "my name is Daksh" → "what is my name" or
"now add tests for it" work as expected. Closing the client (or `/new`)
starts a fresh session; single-shot CLI invocations are one message per
session by design.

## Optional target-repository test command

To run tests after every successful create, write, or edit operation, create `.coding-agent.toml` at the target repository root:

```toml
[test]
command = ["python", "-m", "pytest", "-q"]
timeout_seconds = 60
```

The command is an argument list executed with `shell=False`; model output is never converted into a shell command. A failed test result is returned to the ReAct loop, which may attempt another repair while remaining within `AGENT_MAX_ITERATIONS`.

## Safety boundaries

- All file paths are resolved inside `target_repo`; traversal outside it is rejected.
- `list` and `read` are allowed by default.
- `create`, `write`, and `edit` require explicit `apply_changes: true` authorization at the filesystem boundary.
- Deletion and arbitrary command execution are not available as agent tools.
- Test commands must be opt-in through the target repository's `.coding-agent.toml` and have a timeout.
- The action loop, prompt observation context, and repository summaries are all bounded.

## Current limitations

This project is intentionally scoped for a demonstrable academic prototype.

- MCP servers are started per request and their tool calls are executed, but sessions are not reused across requests. Chat UI and IDE extension are not implemented.
- Sessions are volatile: each run is self-contained, with no memory of previous runs and nothing persisted to disk.
- Repository intelligence currently supports Python AST analysis only.
- ChromaDB semantic memory is deferred until it has a measurable benefit for the demo.
- There is no interactive per-action confirmation UI.
