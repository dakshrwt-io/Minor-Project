# Progress

## Current status

Phase 3 core work is complete. The agent includes a bounded, deterministic Python repository summary in each active prompt.

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

## Current step

Phase 3 core session, context, and Python repository-intelligence work complete. ChromaDB remains deferred because no measurable demo need has been established.

## Open decisions

- Confirm the target repository or repositories to use for the demo.
- Confirm whether an IDE extension is an evaluator requirement or a stretch goal.
- Confirm whether an Anthropic API key is available for manual Phase 1 testing; automated tests will not require it.

## Next step

On explicit instruction, begin Phase 4 with only the MCP client adapter and common tool contract.
