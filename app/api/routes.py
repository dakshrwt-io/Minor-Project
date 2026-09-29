"""HTTP gateway routes for one coding-agent invocation."""

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, status
from fastapi.responses import StreamingResponse

from app.agent import AgentRunner
from app.contracts import AgentRequest


def build_agent_router(runner: AgentRunner) -> APIRouter:
    """Create the gateway router with an explicitly supplied agent runner."""

    router = APIRouter(prefix="/v1/agent", tags=["agent"])

    @router.post("/run/stream", status_code=status.HTTP_200_OK)
    async def run_agent_stream(request: AgentRequest) -> StreamingResponse:
        """Stream one run as server-sent events: plan, action, observation, done.

        Terminal events are `done` (carries the full AgentResponse) and
        `error` (carries the HTTP-equivalent status code: 400 for
        configuration problems, 502 for provider and SDK failures).
        """

        async def event_stream() -> AsyncIterator[str]:
            async for event in runner.run_events(request):
                yield f"data: {json.dumps(event, default=str)}\n\n"
                if event.get("type") in {"done", "error"}:
                    break

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
