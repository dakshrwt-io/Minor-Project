# Autonomous Coding Agent

College minor-project implementation of an auditable coding agent. Phase 1 accepts a natural-language task and an explicit target repository, then runs a bounded Plan → Act → Observe loop using only confined filesystem operations.

## Phase 1 capabilities

- FastAPI gateway: `POST /v1/agent/run`
- Deterministic task planner
- LangGraph-backed ReAct loop with a configurable action limit
- Anthropic model adapter behind a provider-neutral interface
- Filesystem `list`, `read`, `create`, `write`, and exact-match `edit` actions
- Target-root path confinement; deletion and shell execution are unavailable

## Development setup

Create and activate a virtual environment, then install the project with development tools:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Run the checks:

```powershell
python -m pytest tests -q -p no:cacheprovider
python -m ruff check app tests
python -m ruff format --check app tests
```

## Run the API

Set the Anthropic key in the current PowerShell session. `.env.example` documents the available variables; the application currently reads environment variables directly.

```powershell
$env:ANTHROPIC_API_KEY = "your-key"
$env:AGENT_MODEL = "claude-sonnet-4-20250514"
$env:AGENT_MAX_ITERATIONS = "6"
python -m uvicorn app.main:app --reload
```

In a second PowerShell session, send a request. Use an existing repository path as `target_repo`; this agent's source directory is not assumed to be the target.

```powershell
$body = @{
  task = "Read the README and summarize the smallest documentation improvement"
  target_repo = "C:\\path\\to\\demo-repository"
  apply_changes = $false
} | ConvertTo-Json

Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/v1/agent/run" `
  -ContentType "application/json" `
  -Body $body
```

Set `apply_changes` to `$true` only when the agent should be allowed to select the Phase 1 create, write, or edit actions. The response includes the plan, every filesystem observation, a final status, and a summary.

## Current limits

This is a demo-oriented Phase 1 MVP. It has no test runner, user-confirmation workflow, persistence, repository intelligence, MCP integration, UI, or IDE extension yet. The action loop is bounded by `AGENT_MAX_ITERATIONS`; a malformed model action or exhausted limit returns a failed status rather than continuing indefinitely.
