# Progress

## Current status

A post-completion code audit surfaced wrong-way findings (C1–C8). An agreed hardening pass is underway for C5 (native tool-use), C7 (LLM planner), C8 (session memory read-back). Unit A of 4 is complete.

## Completed

- Created the persistent implementation plan with the proposed folder structure, phase boundaries, technical decisions, risks, and test strategy.
- Recorded the default assumptions: Python backend/tools, explicit target repository per request, Anthropic as the initial provider, and terminal client before an IDE extension.
- Added Python packaging metadata, declared Phase 1 runtime dependencies, and configured pytest and Ruff.
- Added an import-level baseline test for the application package.
- Added immutable typed contracts for plans, filesystem calls/results, and gateway request/response data.
- Added environment-backed settings with validation for the agent iteration limit.
- Added a provider-neutral asynchronous model contract, a settings-driven model router, and an Anthropic adapter that lazily imports its SDK for live calls.
- Added the Phase 1 filesystem tool with list, read, create, write, and exact-match edit operations.
- Confined all filesystem paths to an explicit, existing target repository root; deletion and command execution remain unavailable.
- Added the planner abstraction and an initial deterministic implementation that produces inspect, decide, and completion steps without performing side effects.
- Added a prompt builder that supplies the ordered plan, target root, observations, and an explicit filesystem-only JSON action schema to the model.
- Installed the declared runtime dependencies, including LangGraph, FastAPI, and the Anthropic SDK.
- Added a LangGraph-backed Plan → Act → Observe loop with explicit JSON action parsing, observation recording, and a hard filesystem-action limit.
- Added the FastAPI gateway endpoint, production dependency wiring, and fake-model end-to-end coverage for a target-repository read.
- Documented local setup, API launch, the demo request, and Phase 1 limits; manually validated the generated gateway route without a live model call.
- Added a filesystem permission policy that allows list/read operations and requires explicit request authorization for create/write/edit operations.
- Enforced that policy inside the filesystem tool, so mutation blocking does not depend on model prompt compliance.
- Passed the request's `apply_changes` authorization through the orchestrator to the filesystem execution boundary.
- Added optional target-repository test configuration through `.coding-agent.toml` using a shell-free argument list and a bounded timeout.
- Added a subprocess test runner that captures passing, failing, missing-command, and timed-out command outcomes without yet exposing it to the ReAct loop.
- Added typed test-result observations to the agent response and prompt context.
- Configured the ReAct loop to run an opt-in repository test command after every successful create, write, or edit action.
- Verified a failing test observation can inform a subsequent repair, with the existing action limit bounding all retry attempts.
- Added immutable session records with stable IDs, target-root/task metadata, summaries, and timezone-aware timestamps.
- Added a SQLite session-store interface and implementation for creating, retrieving, and updating compact session summaries.
- Wired the default application orchestrator to a configured SQLite session store.
- Created one session per agent invocation, returned its ID in the response, and persisted the final model summary on normal completion.
- Added deterministic observation compaction that retains recent full results and bounds older filesystem/test details in a short prompt summary.
- Added AST-based Python file and import extraction, excluding common generated/environment directories and retaining parse errors as index issues.
- Added top-level function, async-function, class, and variable extraction plus resolved internal import edges between indexed Python modules.
- Added a bounded repository summarizer and passed one target-repository summary through ReAct state into every model prompt.
- Refreshed the README to document the implemented Phases 1–3 capabilities, configuration, request flow, opt-in test command, safety boundaries, and remaining limits.
- Added provider-neutral definitions/results for external tools and an adapter for an initialized MCP client session's tool schemas and results.
- Added validated per-server MCP configuration parsed from the `AGENT_MCP_SERVERS` JSON environment setting.
- Added short-lived stdio MCP server connections (lazy official SDK import) that collect tool schemas and report per-server startup failures without aborting the request.
- Added bounded MCP tool-schema advertisement (count, description, and schema size caps) to the model prompt, marked as awareness-only since calls are not yet executable.
- Wired configured MCP discovery through the orchestrator, prompt builder, and default gateway dependency assembly; documented the new setting in the README and `.env.example`.
- Converted MCP server opening to a request-scoped lifecycle: all configured sessions stay live inside an async context manager, close on exit, and startup failures never block healthy servers.
- Qualified advertised MCP tool names with their server name (`docs.search_docs`), moved prefix stripping into the adapter, and routed model-issued calls back to the owning live session.
- Added `ExternalToolCall` and made `ExternalToolResult` a frozen pydantic model so external observations serialize through the prompt compactor and gateway response.
- Executed model-issued MCP calls in the ReAct observe node with whitelist enforcement: only names collected from a live connection are callable, and unknown names become auditable failed observations.
- Advertised external tools in the system prompt as invocable, with an explicit external-call JSON form alongside the filesystem form; the compactor summarizes external observations like filesystem and test ones.
- Added a standard-library-only terminal client (`python -m client`) that builds the gateway payload, posts to `/v1/agent/run`, and renders the plan, observations, status, and summary with distinct exit codes for completed, failed, and gateway-error outcomes.
- Added `DEMO.md`, a scripted ~15 minute walkthrough covering all five phases, with a keyless track driven by `examples/fake_model_app.py` and an MCP track driven by `examples/mcp_demo_server.py` against `examples/demo-repository`.
- Hardened the terminal client against Windows consoles: stdout is reconfigured to UTF-8 with replacement, so non-cp1252 repository content (for example BOMs or accented text) renders instead of crashing.
- Fixed demo-gateway parity with the production wiring: the example app now passes the SQLite session store and configured MCP servers, so keyless runs persist session rows exactly like a production run.
- Added an interactive Rich-based REPL client (`python -m client --interactive`) with a status header, spinner during agent runs, colored observation rendering for filesystem/test/external results, and slash commands (`/apply`, `/repo`, `/base-url`, `/clear`, `/help`, `/quit`); single-shot mode stays unchanged.
- Documented the REPL in the README and in a new `DEMO.md` section with a guardrail demo (`/apply` off → `permission denied` observations in the UI).
- Hardening Unit A (audit finding C5): added provider-neutral native tool-use types — `ToolSpec` and `ModelToolCall` in `app/models/base.py`, optional `tools` on `ModelRequest`, optional `tool_calls` on `ModelResponse`.
- Hardening Unit A: the Anthropic adapter now forwards `tools` in Anthropic format and parses `tool_use` content blocks into `ModelToolCall`s alongside text blocks.
- Hardening Unit A: the DeepSeek adapter now forwards tools in OpenAI function format and parses `message.tool_calls`; malformed or non-dict argument JSON degrades to empty arguments so tool validation rejects them as auditable failed observations.
- Hardening Unit A: added adapter tests for tool forwarding, tool-call parsing, omitted-tools behavior, malformed DeepSeek arguments, and absent tool calls.
- Hardening Unit B (audit finding C5): the prompt builder now advertises filesystem operations as five native tools (`fs_list`, `fs_read`, `fs_create`, `fs_write`, `fs_edit`) plus external MCP tools via `ModelRequest.tools`; mutation tools are only advertised when the request authorized changes, making the guardrail structural instead of prompt-based; oversized external schemas are replaced with an elision stub instead of truncated invalid JSON.
- Hardening Unit B: the ReAct act node maps `response.tool_calls` onto `ToolCall` (filesystem prefix `fs_*`) or `ExternalToolCall` (everything else, rejected at the observation boundary if unadvertised); a text-only reply is the final summary; an empty reply fails the task; the JSON-in-text extraction, retry sentinel, and fence tolerance were deleted.
- Hardening Unit B: updated all fake models (orchestrator tests, gateway integration test, keyless demo app) to emit `ModelResponse` tool calls; added tests for tool advertisement gating, schema required-argument checks, empty-reply failure, bounded external schema elision, and MCP advertisement via native tools; updated two stale README lines about MCP advertisement.
- Hardening Unit C (audit finding C7): added `ModelTaskPlanner` — one model call with a `submit_plan` native tool, validated into a `TaskPlan` (goal + steps, capped step count), falling back to `DeterministicTaskPlanner` on any unusable reply (text-only, wrong tool, malformed or empty steps).
- Hardening Unit C: the model factory is invoked outside error handling, so configuration failures (missing API key) still propagate as request-time errors instead of being masked by the fallback; provider request failures also propagate and surface as HTTP 502.
- Hardening Unit C: the planner ABC is now asynchronous and receives the repository summary; the graph plan node is async; production wiring (`app.main`) uses `ModelTaskPlanner(router.get_model)` while the keyless demo app keeps the deterministic planner.
- Hardening Unit D (audit finding C8): added `SessionStore.list_recent(target_root, limit=3)` — newest-first, non-empty summaries only, scoped to one resolved target root, empty result for `limit < 1`.
- Hardening Unit D: the orchestrator loads prior session summaries before each run and passes them through ReAct state into the prompt context as `prior_sessions`; the prompt builder includes them, giving the model continuity across runs against the same repository.
- Hardening Unit E (audit findings C3/C4): all blocking synchronous work in async paths now runs via `asyncio.to_thread` — the subprocess test runner and filesystem tool execution in the observe node, repository AST analysis/summarization, and SQLite session-store calls (prior-summary load, session create, summary update) in `run()`. Synchronous component APIs are unchanged.
- Hardening Unit E: added a thread-identity regression test proving repository analysis and session-store writes execute off the event-loop thread; existing subprocess and filesystem tests now cover the threaded paths.

## Files touched

- `plan.md`
- `progress.md`
- `pyproject.toml`
- `README.md`
- `.gitignore`
- `app/__init__.py`
- `tests/unit/test_package.py`
- `app/config.py`
- `app/contracts.py`
- `.env.example`
- `app/memory/context.py`
- `tests/unit/test_observation_compactor.py`
- `app/intelligence/__init__.py`
- `app/intelligence/models.py`
- `app/intelligence/python_analyzer.py`
- `tests/unit/test_python_project_analyzer.py`
- `app/intelligence/summary.py`
- `tests/unit/test_python_project_summarizer.py`
- `app/tools/base.py`
- `app/mcp/__init__.py`
- `app/mcp/adapter.py`
- `app/mcp/connection.py`
- `tests/unit/test_mcp_adapter.py`
- `tests/unit/test_mcp_connection.py`
- `app/tools/base.py`
- `app/contracts.py`
- `app/memory/context.py`
- `app/orchestrator/state.py`
- `client/__init__.py`
- `client/terminal.py`
- `client/interactive.py`
- `client/__main__.py`
- `tests/unit/test_terminal_client.py`
- `tests/unit/test_interactive.py`
- `start_gateway.ps1`
- `DEMO.md`
- `examples/fake_model_app.py`
- `examples/mcp_demo_server.py`
- `examples/demo-repository/README.md`
- `examples/demo-repository/greeting.py`
- `examples/demo-repository/test_greeting.py`
- `examples/demo-repository/.coding-agent.toml`
- `tests/unit/test_contracts.py`
- `app/models/__init__.py`
- `app/models/base.py`
- `app/models/router.py`
- `app/models/anthropic.py`
- `tests/unit/test_model_router.py`
- `app/tools/__init__.py`
- `app/tools/filesystem.py`
- `tests/unit/test_filesystem_tool.py`
- `app/planner/__init__.py`
- `app/planner/service.py`
- `tests/unit/test_task_planner.py`
- `app/prompts/__init__.py`
- `app/prompts/builder.py`
- `tests/unit/test_prompt_builder.py`
- `app/orchestrator/__init__.py`
- `app/orchestrator/state.py`
- `app/memory/__init__.py`
- `app/memory/models.py`
- `app/memory/store.py`
- `tests/unit/test_session_store.py`
- `app/main.py`
- `.env.example`
- `app/orchestrator/graph.py`
- `tests/unit/test_react_orchestrator.py`
- `app/api/__init__.py`
- `app/api/routes.py`
- `app/main.py`
- `tests/integration/test_gateway.py`
- `app/guardrails/__init__.py`
- `app/guardrails/policy.py`
- `app/testing/__init__.py`
- `app/testing/runner.py`
- `tests/unit/test_test_runner.py`
- `app/orchestrator/state.py`

## Validation

- Parsed `pyproject.toml` with Python's TOML parser.
- Compiled the application and test Python files successfully.
- `python -m pytest tests -q -p no:cacheprovider` passed: 19 tests. The sandboxed test runner could not access pytest's temporary directories; the same command passed when run with the required elevated filesystem access.
- Non-live manual validation passed: the FastAPI application loaded and its OpenAPI schema exposes `POST /v1/agent/run`.
- `python -m ruff check app tests` could not run because Ruff is not installed in the current Python environment. It remains pending until the documented development dependencies are installed.
- The Anthropic SDK is installed. Unit tests still use a fake model and do not require an API key.
- `python -m pytest tests -q -p no:cacheprovider` passed: 21 tests, run with elevated filesystem access for pytest temporary target repositories.
- `python -m pytest tests -q -p no:cacheprovider` passed: 27 tests, run with elevated filesystem access for pytest temporary target repositories.
- `python -m pytest tests -q -p no:cacheprovider` passed: 28 tests, run with elevated filesystem access for pytest temporary target repositories.
- `python -m pytest tests -q -p no:cacheprovider` passed: 33 tests, run with elevated filesystem access for pytest temporary target repositories.
- `python -m pytest tests -q -p no:cacheprovider` passed: 34 tests, run with elevated filesystem access for pytest temporary target repositories.
- `python -m pytest tests -q -p no:cacheprovider` passed: 38 tests, run with elevated filesystem access for pytest temporary target repositories.
- `python -m pytest tests -q -p no:cacheprovider` passed: 41 tests, run with elevated filesystem access for pytest temporary target repositories.
- `python -m pytest tests -q -p no:cacheprovider` passed: 42 tests, run with elevated filesystem access for pytest temporary target repositories.
- `python -m pytest tests -q -p no:cacheprovider` passed: 46 tests, run with elevated filesystem access for pytest temporary target repositories.
- README content manually reviewed against the current gateway, guardrails, test runner, session store, and Python analyzer behavior.
- `python -m pytest tests -q -p no:cacheprovider` passed: 49 tests, run with elevated filesystem access for pytest temporary target repositories.
- `python -m pytest tests -q -p no:cacheprovider` passed: 65 tests, covering configured MCP server parsing, discovery, bounded advertisement, and orchestrator wiring.
- `python -m pytest tests -q -p no:cacheprovider` passed: 70 tests, covering server-qualified adapter routing, live connection lifecycle, model-issued MCP execution, and unknown-tool rejection.
- Live smoke test passed: a real stdio MCPServer (official SDK v2) stayed live through a full ReAct run; a fake model issued `demo.echo`, the real server executed it, and text plus structured content returned as an auditable observation. The lazy SDK import keeps all unit tests SDK-free.
- `python -m pytest tests -q -p no:cacheprovider` passed: 77 tests, covering terminal-client payload building, transcript rendering, exit codes, and gateway-error reporting.
- `python -m pytest tests -q -p no:cacheprovider` passed: 78 tests after adding a Unicode-output regression test for the client.
- `python -m pytest tests -q -p no:cacheprovider` passed: 85 tests after adding the interactive REPL (command parsing, transcript markup, payload toggles, repo-switch validation, gateway-error recovery).
- Live smoke test passed: a real uvicorn gateway with a fake model served the terminal client; the client rendered the plan and summary, and exited `0` for a completed agent run.
- Demo walkthrough validated end-to-end: the keyless basic scenario produced read/write/test-fail/write/test-pass observations and a persisted SQLite session row; the MCP scenario executed a real stdio MCPServer tool (`demo.echo`) and returned its output as an observation; the AST summary snippet printed modules, symbols, and import edges for the demo repository.
- Interactive REPL validated live against a real uvicorn gateway: header, panel transcript, session id, guardrail-denied write observations, `/quit` exit code 0.
- Fixed a circular import that only surfaced on a cold `uvicorn app.main:app` boot: `app.contracts` imported `app.tools.base`, which pulled `app.tools.filesystem`, which imports `app.contracts`. The external-tool models now live in `app.contracts` (the dependency root) and `app.tools.base` re-exports them, keeping every existing import path unchanged. Verified: uvicorn boots and serves `/docs` with HTTP 200.
- Added an optional `AGENT_MODEL_BASE_URL` setting that the Anthropic adapter forwards to the SDK as `base_url`, enabling proxies and gateway endpoints; documented in `.env.example` and the README, with a unit test stubbing the SDK module.
- Clarified the missing-key error with actionable guidance (set in the gateway terminal, restart uvicorn, no `.env` loading) and added `start_gateway.ps1`, which warns and exits when the key is missing in the current terminal.
- Converted raw Anthropic SDK failures (construction or request) into `RuntimeError`s at the adapter boundary, and mapped `RuntimeError` in the gateway route to HTTP 502 with the message; a bad key now returns a readable `502` instead of an opaque `500`. Live-verified with a placeholder key.
- Diagnosed a stale-gateway issue: `uvicorn --reload` respawns from the reloader's env snapshot, so env changes require a full restart; documented that in the README.
- Added repository-root `.env` loading (real environment variables always win), covering the key-missing friction; `start_gateway.ps1` now inspects `.env` for the active provider's key before warning.
- Added a `deepseek` provider via the OpenAI-compatible API (`openai` SDK, lazy import): `DEEPSEEK_API_KEY`, default `https://api.deepseek.com`, `AGENT_MODEL_BASE_URL` override, SDK failures wrapped as `RuntimeError`; registered in the model router and documented in the README and `.env.example`. Live-verified: a gateway booted with `AGENT_MODEL_PROVIDER=deepseek` from `.env` and returned the expected key-required 400 (import no longer crashes on the provider value).
- `python -m pytest tests -q -p no:cacheprovider` passed: 100 tests, covering the new native tool-use contract (`ToolSpec`, `ModelToolCall`), Anthropic `tools` forwarding and `tool_use` parsing, DeepSeek function-format forwarding, malformed-argument tolerance, and absent-tool-call behavior.
- `python -m pytest tests -q -p no:cacheprovider` passed: 104 tests, covering the native-tool-use graph and prompt builder (fs tool advertisement gating, MCP advertisement via native tools, schema elision, empty-reply failure) plus all updated fakes; `py_compile` verified the demo app.
- `python -m pytest tests -q -p no:cacheprovider` passed: 117 tests, covering the async `ModelTaskPlanner` (valid submit_plan calls, model inputs, fallback on every unusable-payload shape, step truncation, unrelated tool calls, provider and factory error propagation) alongside all existing suites.
- `python -m pytest tests -q -p no:cacheprovider` passed: 122 tests, covering `list_recent` ordering/filtering/limits, prompt injection of prior session summaries, and end-to-end two-run continuity through the orchestrator with a real SQLite store.
- `python -m pytest tests -q -p no:cacheprovider` passed: 123 tests, including the thread-identity regression test for off-loop blocking work.

## Current status

All five planned phases are implemented, an interactive REPL client ships as the demo UI, and `DEMO.md` scripts the full walkthrough. The post-audit hardening pass has fixed C3/C4 (async blocking I/O), C5 (native tool use), C7 (LLM planner), and C8 (session memory read-back). Streaming live progress (`/v1/agent/run/stream`, loop emit hook, live-rendering clients), a triage router (`app/router/service.py`: chat answered directly, tasks handed to the planner — stops the planner from inventing tasks from "hello"), and REPL session continuity (client-owned session id + bounded volatile `SessionStore`; prior turns feed the router, planner, and loop, so follow-ups like "what is my name" work within one client run) are implemented and tested. The "Model returned an empty response" failure was root-caused (provider reasoning tokens consume the same `max_tokens` budget, so the old 1,024 cap truncated the reply before any content) and fixed: 8,192 default cap, one bounded empty-reply retry in the loop, planner step cap derived from the action budget, `AGENT_MAX_ITERATIONS` raised to 12. An IDE extension remains a stretch goal.

## Current step

Hardening Units A–E are complete and validated. Remaining known audit findings (not yet scheduled): C1/C2 trust-boundary fixes (target-repo allowlist, test-command snapshot — the RCE chain) and the major/minor robustness items.

## Open decisions

- Confirm the target repository or repositories to use for the demo; `examples/demo-repository` is ready as the default.
- Confirm whether an IDE extension is an evaluator requirement or a stretch goal; the terminal client is the current demo UI.
- Confirm whether an Anthropic API key is available for the optional live track in `DEMO.md`.
- Confirm whether a configured MCP server exists beyond the bundled `examples/mcp_demo_server.py`.

## Next step

On explicit instruction: fix C1/C2 (target-repo allowlist + test-command snapshotting — the RCE chain, highest remaining priority), or rehearse `DEMO.md` against the hardened agent.
