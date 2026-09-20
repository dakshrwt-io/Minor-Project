# Autonomous Coding Agent

A demo-oriented autonomous coding agent for a college minor project. It accepts a natural-language task and an explicit target repository, drives a bounded tool-calling loop over guarded filesystem tools, and returns an auditable result through FastAPI. The orchestration layer is built on the [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) (`Agent` + `Runner`), with LiteLLM for multi-provider models.

## Current capabilities

- FastAPI gateway: `POST /v1/agent/run` and `POST /v1/agent/run/stream` (server-sent live events)
- One OpenAI Agents SDK `Agent` driven by `Runner.run_streamed()`: the model decides when to call a tool and when to reply, inside a hard `max_turns` budget (`AGENT_MAX_ITERATIONS`)
- Session continuity in the REPL: one session per client run — follow-up messages reference earlier turns, stored via the SDK's `SQLiteSession` with bounded replay
- Anthropic, DeepSeek, and OpenRouter models through `LitellmModel`, selected by `AGENT_MODEL_PROVIDER`
- Confined filesystem `list`, `read`, `create`, `write`, and exact-match `edit` actions
- Explicit change authorization: mutating tools are not even advertised to the model unless `apply_changes: true`
- Optional repository-owned test command, auto-run after successful file changes and exposed as a `run_tests` tool
- Python AST repository context: files, imports, top-level symbols, internal import edges, and parse issues
- Bounded prompt context: repository summaries, MCP tool advertisements, and session replay are all capped
- MCP servers (stdio or streamable-HTTP) through the SDK's MCP integration: advertised tool schemas are bounded, names are server-qualified, and one failed server never blocks the others
- Terminal clients: single-shot CLI and an interactive Rich-based REPL with slash commands

## Architecture

```text
HTTP gateway (app/api/routes.py)
  → AgentRunner (app/agent.py)
      → OpenAI Agents SDK Agent
          → LitellmModel (Anthropic / DeepSeek / OpenRouter)
          → @function_tool filesystem tools (app/tools/filesystem.py)
          → run_tests tool (app/testing/runner.py)
          → MCPServerStdio / MCPServerStreamableHttp (bounded)
      → BoundedSession over SQLiteSession (conversation memory)
      → repository summary from the AST analyzer (app/intelligence/)
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

## Run the API

From the repository root, either start it directly:

```powershell
python -m uvicorn app.main:app --reload
```

or use the helper, which warns when the configured provider's key is missing in the current terminal:

```powershell
.\start_gateway.ps1
```

The interactive API schema is then available at `http://127.0.0.1:8000/docs`. The root path `/` intentionally returns 404; the agent endpoint is `POST /v1/agent/run`.

> Restart uvicorn after changing environment variables: `--reload` only watches source files, and the worker inherits the launch shell's environment.

## Try it: interactive REPL (Terminal client)

With the gateway running, open the interactive client in a second terminal Where you want to code:

```powershell
python -m client --interactive --target-repo "C:\path\to\demo-repository"
```

The REPL keeps one session for its whole run: every message carries the same session id, the gateway replays prior turns to the model, and follow-ups like "my name is Daksh" → "what is my name" or "now add tests for it" work as expected. Slash commands: `/apply`, `/repo <path>`, `/base-url <url>`, `/clear`, `/new`, `/help`, `/quit`, `/exit`.

## Configuration

Copy values from `.env.example` into your environment. The application reads environment variables directly and also loads a repository-root `.env` file at startup


### Providers

- **Anthropic** (default): `AGENT_MODEL_PROVIDER=anthropic`, `ANTHROPIC_API_KEY`, model names like `claude-sonnet-4-20250514`. Mapped to `LitellmModel(model="anthropic/<name>")`.
- **DeepSeek**: `AGENT_MODEL_PROVIDER=deepseek`, `DEEPSEEK_API_KEY`, `AGENT_MODEL=deepseek-chat` (or `deepseek-reasoner`). Mapped to `LitellmModel(model="deepseek/<name>", base_url=...)`, defaulting to `https://api.deepseek.com` and honoring `AGENT_MODEL_BASE_URL`.
- **OpenRouter**: `AGENT_MODEL_PROVIDER=openrouter`, `OPENROUTER_API_KEY`, model names in `vendor/model` form (e.g. `anthropic/claude-sonnet-4`, `meta-llama/llama-3.3-70b-instruct`; the gateway adds the `openrouter/` LiteLLM prefix itself). Default base URL is `https://openrouter.ai/api/v1`, honored alongside `AGENT_MODEL_BASE_URL`.

`AGENT_MCP_SERVERS` is an optional JSON list of MCP server entries: a stdio server (`name`, `command`, string `args`) or a streamable-HTTP server (`name`, `url`). The agent connects each configured server per request and advertises its tools alongside the filesystem tools with server-qualified names (`mcp_demo__echo`). Advertisements are bounded (tool count, description length, schema size), and one failed server never blocks the others — its error is reported in the agent's instructions and the run continues.

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

Set `apply_changes` to `$true` only when you authorize the agent to create, write, or edit files. The response includes a `session_id`, the task plan, filesystem and test observations, status, and final summary.

## Terminal client

Two modes, both standard-library-and-Rich only, run against a gateway.

Single-shot:

```powershell
python -m client --task "Read the README and summarize the smallest documentation improvement" `
  --target-repo "C:\path\to\demo-repository"
```

The interactive mode is shown in the quick start above. It keeps the gateway,
target repository, and change authorization in a header, streams each action
and observation live as the agent works, and renders the final result in a
colored panel with plan steps, status, session id, and summary.

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

There is no separate triage stage: the single agent decides on its own when a
message is a question to answer in text and when it needs filesystem tools.
Greetings and repository questions are answered without any tool call, so
conversational messages never reach the filesystem. Closing the REPL client
(or `/new`) starts a fresh session; single-shot CLI invocations are one
message per session by design.

## Optional target-repository test command

To run tests after every successful create, write, or edit operation, create `.coding-agent.toml` at the target repository root:

```toml
[test]
command = ["python", "-m", "pytest", "-q"]
timeout_seconds = 60
```

The command is an argument list executed with `shell=False`; model output is never converted into a shell command. After each successful create, write, or edit the test result is returned to the agent as an observation, which may attempt another repair while remaining within `AGENT_MAX_ITERATIONS`. An explicit `run_tests` tool is also advertised for authorized runs.

## Safety boundaries

- All file paths are resolved inside `target_repo`; traversal (including via `..` or symlinks) is rejected on every call.
- `list` and `read` are allowed by default.
- `create`, `write`, and `edit` require explicit `apply_changes: true` authorization; without it those tools are never advertised to the model at all.
- Deletion and arbitrary command execution are not available as agent tools.
- Test commands must be opt-in through the target repository's `.coding-agent.toml` and have a timeout.
- The action budget (`AGENT_MAX_ITERATIONS`), MCP tool advertisements, session replay, and repository summaries are all bounded.

## Current limitations

This project is intentionally scoped for a demonstrable academic prototype.

- MCP servers are connected per request and their tool calls are executed, but sessions are not reused across requests. Chat UI and IDE extension are not implemented.
- Conversation memory is stored in SQLite (`AGENT_SESSION_DB`) so REPL sessions survive a gateway restart; sessions from different client runs remain isolated.
- Repository intelligence currently supports Python AST analysis only.
- ChromaDB semantic memory is deferred until it has a measurable benefit for the demo.
- There is no interactive per-action confirmation UI.