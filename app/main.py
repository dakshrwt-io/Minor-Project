"""FastAPI application factory and ASGI entrypoint."""

from fastapi import FastAPI

from app.api.routes import build_agent_router
from app.config import Settings
from app.models.router import ModelRouter
from app.orchestrator.graph import ReActOrchestrator
from app.planner.service import ModelTaskPlanner
from app.prompts.builder import PromptBuilder
from app.router.service import ModelTaskRouter


def build_default_orchestrator(settings: Settings | None = None) -> ReActOrchestrator:
    """Wire production dependencies from environment-backed settings.

    The planner's step cap is derived from the loop's action budget: every
    plan step costs at least one action, so an 8-step plan under a 6-action
    budget could only ever end at the limit. Capping the plan by the budget
    (at least 3 steps so plans stay meaningful) keeps the two consistent.
    """

    resolved_settings = settings or Settings.from_env()
    router = ModelRouter(resolved_settings)
    return ReActOrchestrator(
        task_router=ModelTaskRouter(router.get_model),
        planner=ModelTaskPlanner(
            router.get_model,
            max_plan_steps=max(3, min(8, resolved_settings.max_agent_iterations)),
        ),
        model_router=router,
        prompt_builder=PromptBuilder(),
        max_iterations=resolved_settings.max_agent_iterations,
        mcp_servers=list(resolved_settings.mcp_servers),
    )


def create_app(orchestrator: ReActOrchestrator | None = None) -> FastAPI:
    """Build an app, allowing tests to supply a fake-model orchestrator."""

    app = FastAPI(title="Autonomous Coding Agent", version="0.1.0")
    app.include_router(build_agent_router(orchestrator or build_default_orchestrator()))
    return app


app = create_app()
