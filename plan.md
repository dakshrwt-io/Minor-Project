# Autonomous Coding Agent — Implementation Plan

## Objective

Build a demoable, auditable autonomous coding agent for a college minor project. The agent accepts a natural-language coding task and a target repository, plans actions, uses controlled tools to inspect and change files, and reports results through a FastAPI gateway. The first usable milestone is a filesystem-only Phase 1 MVP.

## Scope and priorities

- Prioritize a working end-to-end loop and clear component boundaries over production hardening.
- Implement in independently testable phases; do not begin a later phase before its preceding phase is validated.
- Use Python for the agent backend and tools unless the project owner changes this decision.
- Treat a terminal client as the Phase 5 default UI; an IDE extension remains a stretch goal.

## Proposed repository layout

```text
.
├── app/
│   ├── main.py                    # FastAPI application factory and HTTP entrypoint
│   ├── api/
│   │   ├── routes.py              # Gateway endpoints
│   │   └── schemas.py             # Request/response models
│   ├── planner/
│   │   ├── models.py              # Task-plan domain models
│   │   └── service.py             # High-level request → ordered plan
│   ├── orchestrator/
│   │   ├── state.py               # LangGraph state model
│   │   ├── graph.py               # ReAct graph construction
│   │   └── service.py             # Invocation boundary for the gateway
│   ├── prompts/
│   │   └── builder.py             # Context and tool-schema prompt assembly
│   ├── models/
│   │   ├── base.py                # Provider-neutral model interface
│   │   ├── router.py              # Provider/model selection
│   │   └── anthropic.py           # First provider adapter
│   ├── tools/
│   │   ├── base.py                # Tool contracts and result types
│   │   └── filesystem.py          # Read/list/write/edit/create operations
│   ├── guardrails/                # Phase 2 permission policy and enforcement
│   ├── testing/                   # Phase 2 target-repo test runner
│   ├── memory/                    # Phase 3 session/context persistence
│   ├── intelligence/              # Phase 3 AST-based repository analysis
│   ├── mcp/                       # Phase 4 MCP client/tool adapter
│   └── config.py                  # Environment-based application settings
├── tests/
│   ├── unit/
│   └── integration/
├── client/                        # Phase 5 terminal/chat client
├── pyproject.toml
├── .env.example
├── README.md
├── plan.md
└── progress.md
```

Directories scheduled for later phases are intentionally not scaffolded in Phase 1 unless needed by an implemented boundary.

## Phase 1 — filesystem-only MVP

### Acceptance criteria

Given a task and an explicit target repository path, the API can:

1. create an ordered task plan;
2. run a bounded Plan → Act → Observe LangGraph flow;
3. route an LLM request through a provider-neutral interface (Anthropic first);
4. expose only filesystem read/list/write/create/edit capabilities to the loop; and
5. return the plan, actions, observations, and final result to the caller.

### Implementation units

- [x] 1. Document the architecture, proposed layout, assumptions, and phased plan.
- [x] 2. Initialize the Python project and baseline quality tooling (`pyproject.toml`, application package, test configuration).
- [x] 3. Add shared domain models and configuration for tasks, tool actions/results, and API requests.
- [x] 4. Add the provider-neutral model interface, settings-driven model router, and Anthropic adapter skeleton.
- [x] 5. Implement the filesystem tool with target-root path confinement and unit tests.
- [x] 6. Implement the deterministic task-planner interface and initial planner service.
- [x] 7. Implement prompt construction for Phase 1 state and filesystem tool schemas.
- [x] 8. Implement the LangGraph state and bounded Plan → Act → Observe graph using the planner, router, prompts, and filesystem tool.
- [x] 9. Implement the FastAPI gateway endpoint and an end-to-end integration test using a fake model and temporary target repo.
- [x] 10. Add Phase 1 setup/demo documentation and validate the MVP manually.

## Future phases

### Phase 2 — validation and guardrails

- [x] Add a permission-policy model and enforcement at filesystem/tool execution boundaries.
- [x] Add target-repository test-command discovery/configuration and a subprocess test runner.
- [x] Feed test results back into the ReAct observation state with bounded retry behavior.

### Phase 3 — repository awareness

- [x] Add session state persistence and context compaction/summarization.
  - [x] Add immutable session records plus a SQLite storage interface and implementation.
  - [x] Connect session creation and final summaries to gateway/orchestrator execution.
  - [x] Add bounded context compaction before prompt construction.
- [ ] Add optional ChromaDB-backed semantic memory only if it improves the demo measurably.
- [x] Extract Python AST structure, imports, and symbols; make summaries available to prompts.
  - [x] Extract Python file structure, imports, and recoverable parse issues.
  - [x] Extract top-level symbols and relationships.
  - [x] Make bounded repository summaries available to prompts.

### Phase 4 — MCP

- [ ] Add an MCP client adapter conforming to the common tool contract.
- [ ] Support configured external MCP servers and surface their schemas to the prompt builder.

### Phase 5 — interaction layer

- [ ] Add a minimal chat/terminal client for the gateway.
- [ ] Assess an IDE extension only after the core demo is stable.

## Technical decisions

- **LangGraph:** keep the graph bounded with a maximum iteration count. Phase 1 should use structured model output for tool decisions rather than a free-form shell.
- **Filesystem:** operations must be confined to the request's resolved target root. Phase 1 allows only the operations required for the MVP; deletion and arbitrary command execution are deferred to Phase 2 policy work.
- **Model providers:** application code depends on an internal provider interface. Anthropic SDK configuration is loaded from environment variables; tests use a fake implementation and need no API key.
- **Target repositories:** never assume the agent operates on its own source directory. The target root is explicitly supplied per request and is kept separate from the agent service repo.
- **Storage:** defer ChromaDB and SQLite until Phase 3; early adoption would add setup cost before there is useful context to persist.

## Risks and deadline guidance

- Building a real autonomous open-ended loop before robust boundaries is risky. The Phase 1 loop will be deliberately bounded and filesystem-only.
- Full semantic dependency graphs across arbitrary languages are out of scope for the initial intelligence feature. Start with Python AST and fall back to structural summaries.
- A React client and IDE extension can dilute the core demo. A terminal client is sufficient for Phase 5 unless evaluator requirements demand an extension.
- Model-driven edit application needs auditable action records and deterministic tests; fake-model integration tests are mandatory before relying on live providers.

## Testing strategy

- Unit-test component contracts, path confinement, planner behavior, prompt composition, and graph transitions with fake model responses.
- Add an integration test for the FastAPI endpoint against a disposable target repository.
- Use a live Anthropic smoke test only as an opt-in manual validation, never as the normal automated test suite.

## Future improvements

- Add optional ChromaDB memory only after it has a measurable demo benefit; retain the current SQLite/session and deterministic repository context otherwise.
- Add provider fallbacks, streaming events, user confirmation UX, richer language analysis, git-aware diffs, and an IDE extension after the core workflow is proven.
