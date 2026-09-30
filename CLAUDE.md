# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

Simple autonomous coding agent (college minor project). The user is a beginner-intermediate
coder who must explain every line to professors, so **simplicity beats cleverness**.

A FastAPI server receives a message + a target repository folder, runs a hand-written agent
loop against OpenRouter (via the `openai` library), and streams events back to a terminal REPL.

## Commands (Windows / PowerShell, Python >= 3.11)

```powershell
python -m pip install -e ".[dev]"                   # install
python -m pytest tests -q -p no:cacheprovider       # tests (no network, no API key needed)
python -m ruff check app tests client.py examples   # lint
python -m ruff format --check app tests client.py   # format check
python -m uvicorn app.main:app --reload             # start server
python client.py --repo "C:\path\to\project"         # start chat (second terminal)
```

## Layout

```text
app/config.py        settings as plain variables from .env (OPENROUTER_API_KEY, AGENT_MODEL, ...)
app/tools.py         safe_path() + 5 file tools + TOOL_DESCRIPTIONS + run_tool()
app/agent.py         run_agent(): THE loop (async generator yielding event dicts)
app/memory.py        SQLite: load_messages / save_messages / trim
app/repo_summary.py  AST map of Python files, added to the system prompt on the first message
app/mcp_client.py    McpConnections: start stdio MCP servers, list + call their tools
app/main.py          FastAPI POST /run, streams Server-Sent Events
client.py            Rich terminal REPL (/apply, /repo, /new, /help, /quit)
examples/demo_mcp_server.py   demo MCP server with a current_time tool
tests/               one test file per app module; FakeClient in test_agent.py fakes the model
```

## Rules for changes

- Keep code simple: plain functions, dicts, strings. A class only when it must hold state.
  No new abstractions or libraries without a clear reason the user can explain.
- Safety is in code, not in the prompt: every path goes through `safe_path()`; write tools
  exist only when `apply_changes=True`; there is no delete or shell tool.
- Tools return text; errors are text starting with `"Error:"`, never exceptions to the loop.
- Everything sent to the model is size-limited (file reads, repo map, MCP tools, memory).
- Memory is saved only after a run finishes (a failed run must not break a session).
- The user adds their own comments (sometimes Hindi/English mix) to files. Never remove or
  rewrite them. If they break lint, tell the user instead of changing them.
- Teaching style: build in small steps, explain each change, ask 3 check questions.
- Commits must look authored by the user: no Claude co-author line.
- Never read, print, or commit `.env` (real API key). `data/` is runtime state (gitignored).
- Old, more complex version lives on the `main` branch; this work is on `simplify`.
