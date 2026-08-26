"""FastAPI application factory and ASGI entrypoint."""

from fastapi import FastAPI

from app.api.routes import build_agent_router
from app.config import Settings
from app.memory.store import SqliteSessionStore
from app.models.router import ModelRouter
from app.orchestrator.graph import ReActOrchestrator
from app.planner.service import DeterministicTaskPlanner
from app.prompts.builder import PromptBuilder


def build_default_orchestrator(settings: Settings | None = None) -> ReActOrchestrator:
    """Wire production dependencies from environment-backed settings."""

    resolved_settings = settings or Settings.from_env()
    return ReActOrchestrator(
        planner=DeterministicTaskPlanner(),
        model_router=ModelRouter(resolved_settings),
        prompt_builder=PromptBuilder(),
        max_iterations=resolved_settings.max_agent_iterations,
        session_store=SqliteSessionStore(resolved_settings.session_database_path),
    )


def create_app(orchestrator: ReActOrchestrator | None = None) -> FastAPI:
    """Build an app, allowing tests to supply a fake-model orchestrator."""

    app = FastAPI(title="Autonomous Coding Agent", version="0.1.0")
    app.include_router(build_agent_router(orchestrator or build_default_orchestrator()))
    return app


app = create_app()
