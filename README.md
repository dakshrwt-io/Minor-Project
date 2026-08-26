# Autonomous Coding Agent

A demo-oriented autonomous coding agent for a college minor project. It accepts a natural-language task and an explicit target repository, plans a bounded sequence of actions, uses guarded filesystem tools, and returns an auditable result through FastAPI.

## Current capabilities

- FastAPI gateway: `POST /v1/agent/run`
- Bounded LangGraph Plan → Act → Observe loop
- Anthropic model adapter behind a provider-neutral model router
- Confined filesystem `list`, `read`, `create`, `write`, and exact-match `edit` actions
- Explicit change authorization: mutations require `apply_changes: true`
- Optional repository-owned test command, run after successful file changes
- SQLite session record for every default execution, including its final summary
- Python AST repository context: files, imports, top-level symbols, internal import edges, and parse issues
- Bounded prompt context for observations and repository summaries

## Architecture

```text
HTTP gateway
  → task planner
  → LangGraph ReAct orchestrator
      → prompt builder → model router → Anthropic
      → guarded filesystem tool
      → optional target-repository test runner
      → SQLite session store
      → Python AST repository analyzer
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

Copy values from `.env.example` into your environment. The application reads environment variables directly; it does not load a `.env` file itself.

```powershell
$env:ANTHROPIC_API_KEY = "your-key"
$env:AGENT_MODEL_PROVIDER = "anthropic"
$env:AGENT_MODEL = "claude-sonnet-4-20250514"
$env:AGENT_MAX_ITERATIONS = "6"
$env:AGENT_SESSION_DATABASE = "data/agent-state.sqlite3"
```

`ANTHROPIC_API_KEY` is required only for live model-backed runs. Unit and integration tests use fake models and require no key.

## Run the API

```powershell
python -m uvicorn app.main:app --reload
```

The interactive API schema is then available at `http://127.0.0.1:8000/docs`.

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

- MCP integration, chat UI, and IDE extension are not implemented yet.
- Session records are persisted but previous sessions are not reused as active prompt context.
- Repository intelligence currently supports Python AST analysis only.
- ChromaDB semantic memory is deferred until it has a measurable benefit for the demo.
- There is no streaming response protocol or interactive per-action confirmation UI.
