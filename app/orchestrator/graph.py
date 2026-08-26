"""A bounded LangGraph Plan → Act → Observe loop for the Phase 1 MVP."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from app.contracts import (
    AgentRequest,
    AgentResponse,
    FilesystemOperation,
    TaskStatus,
    TestResult,
    ToolCall,
    ToolResult,
)
from app.models.base import ModelClient
from app.models.router import ModelRouter
from app.memory.models import SessionRecord
from app.memory.store import SessionStore
from app.orchestrator.state import ReActState
from app.planner.service import TaskPlanner
from app.prompts.builder import PromptBuilder
from app.testing.runner import TestRunner
from app.tools.filesystem import FilesystemTool


class ReActOrchestrator:
    """Run a request through a bounded Plan → Act → Observe graph."""

    def __init__(
        self,
        *,
        planner: TaskPlanner,
        model_router: ModelRouter,
        prompt_builder: PromptBuilder,
        max_iterations: int,
        session_store: SessionStore | None = None,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")
        self._planner = planner
        self._model_router = model_router
        self._prompt_builder = prompt_builder
        self._max_iterations = max_iterations
        self._session_store = session_store

    async def run(self, request: AgentRequest) -> AgentResponse:
        """Execute one agent request against its explicitly supplied target root."""

        target_root = request.target_repo.resolve()
        session = self._create_session(target_root, request.task)
        tool = FilesystemTool(target_root, allow_changes=request.apply_changes)
        model = self._model_router.get_model()
        graph = self._build_graph(tool, model)
        result = await graph.ainvoke(
            {
                "task": request.task,
                "target_root": target_root,
                "apply_changes": request.apply_changes,
                "plan": None,
                "observations": [],
                "pending_call": None,
                "summary": "",
                "status": None,
                "iterations": 0,
            }
        )
        plan = result["plan"]
        status = result["status"]
        if plan is None or status is None:
            raise RuntimeError("ReAct graph completed without a plan or status")
        response = AgentResponse(
            session_id=session.session_id if session is not None else None,
            plan=plan,
            status=status,
            observations=result["observations"],
            summary=result["summary"],
        )
        if session is not None:
            self._session_store.update_summary(session.session_id, response.summary)
        return response

    def _build_graph(self, tool: FilesystemTool, model: ModelClient):
        graph = StateGraph(ReActState)

        def plan_node(state: ReActState) -> dict[str, object]:
            return {
                "plan": self._planner.create_plan(
                    state["task"], apply_changes=state["apply_changes"]
                )
            }

        async def act_node(state: ReActState) -> dict[str, object]:
            plan = state["plan"]
            if plan is None:
                return self._failed_action("Cannot act before creating a task plan")
            prompt = self._prompt_builder.build(
                plan=plan,
                target_root=state["target_root"],
                apply_changes=state["apply_changes"],
                observations=state["observations"],
            )
            response = await model.complete(prompt)
            return self._parse_action(response.text)

        def observe_node(state: ReActState) -> dict[str, object]:
            call = state["pending_call"]
            if call is None:
                return self._failed_action("Cannot observe without a pending tool call")
            result = tool.execute(call)
            observations: list[ToolResult | TestResult] = [*state["observations"], result]
            if result.succeeded and call.operation in {
                FilesystemOperation.CREATE,
                FilesystemOperation.WRITE,
                FilesystemOperation.EDIT,
            }:
                test_result = self._run_configured_tests(state["target_root"])
                if test_result is not None:
                    observations.append(test_result)
            return {
                "observations": observations,
                "pending_call": None,
                "iterations": state["iterations"] + 1,
            }

        def limit_node(_: ReActState) -> dict[str, object]:
            return {
                "status": TaskStatus.FAILED,
                "summary": f"Stopped after reaching the {self._max_iterations}-action limit.",
            }

        def route_after_act(state: ReActState) -> Literal["observe", "end"]:
            return "observe" if state["pending_call"] is not None else "end"

        def route_after_observe(state: ReActState) -> Literal["act", "limit"]:
            return "limit" if state["iterations"] >= self._max_iterations else "act"

        graph.add_node("plan", plan_node)
        graph.add_node("act", act_node)
        graph.add_node("observe", observe_node)
        graph.add_node("limit", limit_node)
        graph.add_edge(START, "plan")
        graph.add_edge("plan", "act")
        graph.add_conditional_edges("act", route_after_act, {"observe": "observe", "end": END})
        graph.add_conditional_edges(
            "observe", route_after_observe, {"act": "act", "limit": "limit"}
        )
        graph.add_edge("limit", END)
        return graph.compile()

    @staticmethod
    def _parse_action(text: str) -> dict[str, object]:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return ReActOrchestrator._failed_action("Model response was not valid JSON")

        if payload.get("kind") == "final" and isinstance(payload.get("summary"), str):
            return {"status": TaskStatus.COMPLETED, "summary": payload["summary"]}
        if payload.get("kind") == "tool_call":
            try:
                call = ToolCall.model_validate(
                    {
                        "tool_name": payload.get("tool_name"),
                        "operation": payload.get("operation"),
                        "path": payload.get("path"),
                        "arguments": payload.get("arguments", {}),
                    }
                )
            except ValidationError:
                return ReActOrchestrator._failed_action("Model returned an invalid tool call")
            return {"pending_call": call}
        return ReActOrchestrator._failed_action("Model returned an unsupported action")

    def _create_session(self, target_root: Path, task: str) -> SessionRecord | None:
        if self._session_store is None:
            return None
        session = SessionRecord(target_root=target_root, task=task)
        self._session_store.create(session)
        return session

    @staticmethod
    def _run_configured_tests(target_root: Path) -> TestResult | None:
        """Run the repository's opt-in test command after a successful mutation."""

        try:
            command = TestRunner.discover(target_root)
        except ValueError as exc:
            return TestResult(
                command=[],
                passed=False,
                return_code=None,
                error=f"test configuration error: {exc}",
            )
        if command is None:
            return None

        outcome = TestRunner(target_root, command).run()
        return TestResult(
            command=list(outcome.command.arguments),
            passed=outcome.passed,
            output=outcome.output,
            return_code=outcome.return_code,
            timed_out=outcome.timed_out,
            error=outcome.error,
        )

    @staticmethod
    def _failed_action(summary: str) -> dict[str, object]:
        return {"status": TaskStatus.FAILED, "summary": summary}
