"""FastAPI application factory and ASGI entrypoint."""

from fastapi import FastAPI

from app.agent import AgentRunner
from app.api.routes import build_agent_router
from app.config import Settings


def build_default_agent_runner(settings: Settings | None = None) -> AgentRunner:
    """Wire production dependencies from environment-backed settings."""

    return AgentRunner(settings or Settings.from_env())


def create_app(runner: AgentRunner | None = None) -> FastAPI:
    """Build an app, allowing tests to supply a fake-model runner."""

    app = FastAPI(title="Autonomous Coding Agent", version="0.2.0")
    app.include_router(build_agent_router(runner or build_default_agent_runner()))
    return app


app = create_app()
