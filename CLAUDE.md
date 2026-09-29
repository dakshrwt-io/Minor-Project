# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

Autonomous coding agent (college minor project). A FastAPI gateway takes a natural-language
task plus an explicit `target_repo`, runs one OpenAI Agents SDK `Agent` via
`Runner.run_streamed()` over guarded filesystem tools, and streams an auditable result as SSE.
Models go through LiteLLM (`LitellmModel`): Anthropic (default), DeepSeek, OpenRouter.

The agent's target repository is always supplied per request. This repository is never the target.

## Commands

Python >= 3.11 (dev machine runs 3.14). Windows / PowerShell.

```powershell
python -m pip install -e ".[dev]"                 # install
python -m pytest tests -q -p no:cacheprovider     # tests (fast, no network, no API key)
python -m pytest tests/unit/test_agent_runner.py -q -p no:cacheprovider   # single file
python -m ruff check app tests client              # lint
python -m ruff format --check app tests client     # format check
python -m uvicorn app.main:app --reload            # run gateway (or .\start_gateway.ps1)
python -m client --interactive --target-repo "C:\path\to\repo"   # REPL client
python -m client --task "..." --target-repo "C:\path\to\repo"    # single-shot client
```

- pytest uses `--basetemp=.test-tmp` (set in `pyproject.toml`).
- Ruff currently reports pre-existing errors (E501 long lines, UP042 `str, Enum`). Don't
  introduce new ones; don't mass-fix unrelated ones unless asked.
- Restart uvicorn after changing env vars; `--reload` only watches source files.

## Layout

```text
app/
  main.py              create_app(runner=None) factory; module-level `app` for uvicorn
  api/routes.py        POST /v1/agent/run/stream (only endpoint; SSE)
  config.py            Settings.from_env(), .env loader, AGENT_MCP_SERVERS parsing
  contracts.py         Pydantic boundary types: AgentRequest/Response, ToolResult, TestResult,
                       ExternalToolResult, TaskPlan
  agent/
    runner.py          AgentRunner (run / run_events / _run_core), GuardedAgent, repeat guard,
                       build_agent(), per-request user payload
    tools.py           @function_tool wrappers: fs_list, fs_read (+ fs_create, fs_write, fs_edit,
                       run_tests only when apply_changes); auto-run tests after mutations
    prompts.py         SYSTEM_INSTRUCTIONS_TEMPLATE, build_model() provider selection
    mcp.py             BoundedMCPServer: caps tool count / description / schema size
    session.py         BoundedSession over SQLiteSession (40-item window, 800-char clipping)
    observations.py    decode tool output JSON envelopes into contract records
  tools/filesystem.py  FilesystemTool: path confinement + permission checks, never raises
  testing/runner.py    .coding-agent.toml discovery, shell-free test execution with timeout
  intelligence/        Python AST analyzer + bounded summarizer (repo context for the prompt)
client/                stdlib + Rich terminal clients (terminal.py single-shot, interactive.py
                       REPL, formatting.py shared rendering)
tests/unit, tests/integration
```

## Request flow

1. `routes.py` receives `AgentRequest` and iterates `AgentRunner.run_events()`.
2. `_run_core` resolves `target_repo`, builds an AST summary (in a thread), opens a
   `BoundedSession`, connects MCP servers (each failure isolated and reported in instructions).
3. `build_agent` assembles tools + instructions. The agent runs with `max_turns = AGENT_MAX_ITERATIONS`.
4. Stream events are turned into SSE events: `plan` -> `action` / `observation`* -> `done` | `error`.
   - `MaxTurnsExceeded` -> `done` with status `failed`.
   - `ValueError` -> `error` 400 (configuration, e.g. missing API key).
   - anything else -> `error` 502 (provider / SDK failure, wrapped as `RuntimeError`).

## Invariants — do not break

- **Safety is structural, not prompt-based.** Without `apply_changes`, mutating tools are never
  constructed or advertised. Keep it that way; don't gate them by instructions alone.
- Every path goes through `FilesystemTool._resolve_path` confinement (rejects `..`, absolute
  paths elsewhere, symlink escapes).
- No delete, shell, or network tools for the agent. Test commands come only from the target
  repo's `.coding-agent.toml`, run with `shell=False` and a timeout. Never build a command from
  model text.
- Tools report failures as results (`ToolResult` / `ExternalToolResult` with `error`), never as
  crashes. Contracts enforce "succeeded implies no error, failed implies error".
- Tool outputs are JSON envelopes `{"kind": "filesystem"|"test"|"external", "result": ...}`;
  `observations.py` decodes them. Change both sides together.
- Everything reaching the prompt is bounded: repo summary, MCP advertisements, session replay.
- Repeat guard blocks back-to-back identical tool calls (`_apply_repeat_guard`), including MCP tools
  via `GuardedAgent.get_all_tools`.
- SDK tracing is disabled (`set_tracing_disabled(True)`); service stays self-contained.
- `app/agent/__init__.py` re-exports internals (some `_private`) that tests import. Update it when
  moving or renaming symbols.

## Testing conventions

- No real model calls. Tests use `agents.testing.model.ScriptedModel` with `function_call(...)` /
  `assistant_message(...)` and pass it via `AgentRunner(settings, model=...)`.
- Build settings with `Settings.from_env({...})` and `AGENT_SESSION_DB=":memory:"` so tests
  don't read the machine's environment or `.env`.
- Target repos are created under pytest `tmp_path`.
- Gateway tests use `create_app(runner)` + `TestClient.stream("POST", "/v1/agent/run/stream", ...)`.
- MCP tests spawn a tiny stdio echo server written to `tmp_path` (see `test_agent_runner.py`).

## Configuration

Env vars (see `.env.example`; a repo-root `.env` is loaded, real env wins):
`AGENT_MODEL_PROVIDER`, `AGENT_MODEL`, `ANTHROPIC_API_KEY` / `DEEPSEEK_API_KEY` /
`OPENROUTER_API_KEY`, `AGENT_MODEL_BASE_URL`, `AGENT_MAX_ITERATIONS` (default 6),
`AGENT_SESSION_DB` (default `data/agent-state.sqlite3`), `AGENT_MCP_SERVERS` (JSON list).

Never read, print, or commit `.env`; it holds real keys. `data/` and `*.sqlite3` are runtime state
(gitignored).

## Style

- `from __future__ import annotations`, full type hints, frozen Pydantic models / frozen slotted
  dataclasses for records.
- Module docstring on every file; docstrings explain *why* (safety reasoning, bounds).
- Line length 100, double quotes, ruff rules E, F, I, UP.
- Keep scope small: this is a demo-scoped academic prototype (see README "Current limitations").

## Known doc drift

- README "Architecture" block still says `app/agent.py`; the code is the `app/agent/` package.
- `ARCHITECTURE.md` is deleted in the working tree (it described the removed non-streaming
  `POST /v1/agent/run` endpoint and the old single-module layout).
