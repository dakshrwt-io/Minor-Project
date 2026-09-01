# Demo Walkthrough

A ~17 minute scripted demo of the autonomous coding agent, built around two
tracks. **Track A needs no API key** and proves the full pipeline with a
scripted model; **Track B** swaps in a live Anthropic model. All commands are
PowerShell and assume the repository root (`C:\Minor Project`).

| Section | What it proves | Time |
|---|---|---|
| 0. Setup | install + automated tests | 5 min |
| 1. Keyless run | Phases 1–3: planner, filesystem, guardrails, test runner, memory, intelligence | 5 min |
| 1b. Interactive REPL | Phase 5 UI: Claude-Code-style terminal client | 2 min |
| 2. Persistence + intelligence inspection | Phase 3 internals | 2 min |
| 3. MCP run | Phase 4: external tool execution | 3 min |
| 4. Live model (optional) | Phase 5 client + real LLM | 3 min |

## 0. Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Verify the automated suite (77 tests, no API key, no external server):

```powershell
python -m pytest tests -q -p no:cacheprovider
```

## 1. Keyless end-to-end run (Phases 1–3)

Open a terminal at the repository root and launch the demo gateway (port 8765,
so it never collides with a real gateway on 8000):

```powershell
python examples\fake_model_app.py
```

In a second terminal, run the agent through the terminal client:

```powershell
python -m client --task "Fix the greeting in greeting.py so the test passes" `
  --target-repo "C:\Minor Project\examples\demo-repository" `
  --apply-changes --base-url "http://127.0.0.1:8765"
```

Expected transcript (plan arrives first, then each action streams live as the
agent works):

```
Task: Fix the greeting in greeting.py so the test passes
  1. [pending] Inspect target-repository files relevant to: ...
  2. [pending] Determine a minimal, safe change for: ...
  3. [pending] Apply the smallest filesystem change that satisfies the request.
→ fs_read greeting.py
  - filesystem read greeting.py: succeeded; def greet(): return "Hello"
→ fs_write greeting.py
  - filesystem write greeting.py: succeeded; wrote greeting.py
  - test command: failed; ... 1 failed ...
→ fs_write greeting.py
  - filesystem write greeting.py: succeeded; wrote greeting.py
  - test command: passed; 1 passed
Status: completed
Session: 80d1ae81-182c-4c29-b2ec-e472cb416f3e   (a new UUID per run)
Summary: greeting.py now returns the expected greeting.
```

What each line demonstrates:

- **Plan** — the Task Planner grounded the request in the repository summary
  before any action.
- **`→ fs_read` / `→ fs_write` lines** — the gateway streams every action and
  observation over `POST /v1/agent/run/stream` (server-sent events); the
  client prints them the moment they happen instead of after the run.
- **`filesystem read`** — Phase 1 tooling: confined read inside `target_repo`.
- **`filesystem write`** — Phase 2 guardrails: the write only happened because
  `--apply-changes` authorized it; without the flag the write is denied.
- **`test command: failed` → second write → `test command: passed`** — Phase 2
  loop: after every successful mutation the opt-in test runner
  (`.coding-agent.toml` → `python -m pytest -q`) fed the failure back as an
  observation, and the loop iterated until green, bounded by
  `AGENT_MAX_ITERATIONS`.
- **`Status: completed` + summary** — Phase 3: the run is a self-contained
  session — the `session_id` is returned in the response and nothing is
  persisted.
- **Terminal transcript** — Phase 5: the client is the demo UI.

The demo repository now contains the fixed `greeting.py`. Before re-running,
reset it (ascii encoding, no BOM):

```powershell
@"
def greet():
    return "Hello"
"@ | Set-Content -LiteralPath "examples\demo-repository\greeting.py" -Encoding ascii
```

## 1b. Interactive REPL (Phase 5 UI)

With the demo gateway still running, open the Claude-Code-style REPL:

```powershell
python -m client --interactive `
  --target-repo "C:\Minor Project\examples\demo-repository" `
  --base-url "http://127.0.0.1:8765"
```

The header shows the gateway, target, and change-authorization state. Type
`/apply` to authorize edits, then submit the same task and press Enter:

```
❯ Fix the greeting in greeting.py so the test passes
```

A spinner is replaced by live streaming: each action and observation prints in
color as it happens, then the result renders in a panel:
plan steps, colored observations (`✓`/`✗` per filesystem, test, and external
tool), status, session id, and summary. Useful commands: `/repo <path>`,
`/base-url <url>`, `/clear`, `/help`, `/quit`. Notice that without `/apply`,
write attempts appear as `permission denied` observations — the guardrail
holds in the UI too. Type `/quit` to leave.

### 1c. Conversation is not a task (triage router)

Against a gateway running the real model (not the scripted demo gateway),
type a greeting in the REPL:

```
❯ hello
```

Expected: a short conversational reply, no plan steps, no actions:

```
Status: completed
Summary: Hey! I'm ready to help with this repository. What would you like
me to build or fix?
```

The triage router classifies the message before the planner runs, so a
"hello" is never turned into an invented "implement a hello program" task.
The scripted demo gateway deliberately skips triage (deterministic router)
to keep its canned transcript stable.

### 1d. One conversation, one session

Still in the REPL (real-model gateway), show that messages share a session.
The header prints the session id the client reuses for every message. Then:

```
❯ my name is Daksh
❯ what is my name?
```

Expected: the second answer uses the first turn — the gateway keeps the
session's bounded prior turns and feeds them to the router, planner, and
loop. Follow-up coding tasks work too ("now add tests for it"). Close the
client and reopen it: the header shows a new session id and the earlier
turns are gone (memory is volatile by design). `/new` restarts the session
without leaving the REPL.

## 2. Inspect repository intelligence (Phase 3)

Sessions are volatile by design — nothing is written to disk. Show the
AST-based repository intelligence that went into the model prompt:

```powershell
python -c "from pathlib import Path; from app.intelligence.python_analyzer import PythonProjectAnalyzer; from app.intelligence.summary import PythonProjectSummarizer; print(PythonProjectSummarizer().summarize(PythonProjectAnalyzer().analyze(Path('examples/demo-repository'))))"
```

Expected: module list, top-level symbols (`function greet`, `function test_greeting`),
and the internal import edge `test_greeting -> greeting`.

## 3. MCP external tool run (Phase 4)

Stop the demo gateway (`Ctrl+C`) and restart it with MCP enabled:

```powershell
$env:AGENT_MCP_SERVERS = '[{"name":"demo","command":"python","args":["examples/mcp_demo_server.py"]}]'
$env:FAKE_MODEL_SCENARIO = "mcp"
python examples\fake_model_app.py
```

Run a second task:

```powershell
python -m client --task "Echo a message through the external demo tool" `
  --target-repo "C:\Minor Project\examples\demo-repository" `
  --base-url "http://127.0.0.1:8765"
```

Expected observation:

```
  - external demo.echo: succeeded; echo: hello from the agent
```

What this proves: the gateway spawned the real stdio MCP server
(`examples\mcp_demo_server.py`, official SDK v2), advertised its tool as the
server-qualified name `demo.echo` in the prompt, executed the model-issued call
against the live session, and returned an auditable observation. Only
advertised names are callable; anything else becomes a failed observation.

Stop the gateway and clear the variables:

```powershell
$env:AGENT_MCP_SERVERS = $null
$env:FAKE_MODEL_SCENARIO = $null
```

## 4. Live model run (Phase 5, optional)

Needs an Anthropic API key. Start the real gateway:

```powershell
$env:ANTHROPIC_API_KEY = "your-key"
python -m uvicorn app.main:app --reload
```

Then drive it with the same client (default base URL is port 8000):

```powershell
python -m client --task "Read README.md and propose the smallest improvement" `
  --target-repo "C:\Minor Project\examples\demo-repository"
```

Add `--apply-changes` only when you authorize edits. Client exit codes:
`0` completed, `1` agent failed, `2` client or gateway error.

## Evaluator checklist

- [ ] Automated suite passes: `python -m pytest tests -q -p no:cacheprovider`
- [ ] Plan appears before any tool action
- [ ] Read/write observations show real file contents and paths inside the target repo
- [ ] Test failures feed back and the loop retries until green
- [ ] Response includes a fresh `session_id` (new id every run, nothing persisted)
- [ ] AST summary shows modules, symbols, and import edges
- [ ] MCP observation proves a real external server executed the call
- [ ] Client prints a readable transcript and meaningful exit codes

## Troubleshooting

- **Port 8765 busy** — a previous demo gateway is still running: `Get-Process python | Stop-Process` (or close the window).
- **`ModuleNotFoundError: app`** — commands must run from the repository root, not from `examples\`.
- **Tests fail in the demo repo** — re-run the reset command from section 1 before repeating the basic scenario.
- **MCP observation says `unknown external tool`** — `AGENT_MCP_SERVERS` was not set in the gateway's terminal before launch.
