"""HTTP gateway routes for one coding-agent invocation."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse

from app.contracts import AgentRequest, AgentResponse
from app.orchestrator.graph import ReActOrchestrator


def build_agent_router(orchestrator: ReActOrchestrator) -> APIRouter:
    """Create the gateway router with an explicitly supplied orchestrator."""

    router = APIRouter(prefix="/v1/agent", tags=["agent"])

    @router.post("/run", response_model=AgentResponse, status_code=status.HTTP_200_OK)
    async def run_agent(request: AgentRequest) -> AgentResponse:
        try:
            return await orchestrator.run(request)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
            ) from exc

    @router.post("/run/stream", status_code=status.HTTP_200_OK)
    async def run_agent_stream(request: AgentRequest) -> StreamingResponse:
        """Stream one run as server-sent events: plan, action, observation, done.

        The non-streaming `/run` endpoint stays the contract of record; this
        transport only adds live progress. Terminal events are `done` (carries
        the full AgentResponse) and `error` (carries the HTTP-equivalent code).
        """

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def emit(event: dict[str, Any]) -> None:
            await queue.put(event)

        async def drive() -> None:
            try:
                response = await orchestrator.run(request, emit=emit)
            except ValueError as exc:
                await queue.put(
                    {
                        "type": "error",
                        "status_code": status.HTTP_400_BAD_REQUEST,
                        "detail": str(exc),
                    }
                )
            except RuntimeError as exc:
                await queue.put(
                    {
                        "type": "error",
                        "status_code": status.HTTP_502_BAD_GATEWAY,
                        "detail": str(exc),
                    }
                )
            else:
                await queue.put(
                    {
                        "type": "done",
                        "status": response.status.value,
                        "summary": response.summary,
                        "response": response.model_dump(mode="json"),
                    }
                )

        async def event_stream() -> AsyncIterator[str]:
            runner = asyncio.create_task(drive())
            try:
                while True:
                    event = await queue.get()
                    yield f"data: {json.dumps(event, default=str)}\n\n"
                    if event.get("type") in {"done", "error"}:
                        break
            finally:
                if not runner.done():
                    runner.cancel()

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
