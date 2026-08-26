"""HTTP gateway routes for one coding-agent invocation."""

from fastapi import APIRouter, HTTPException, status

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

    return router
