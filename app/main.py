"""Web API: the server the terminal client talks to.

One endpoint, POST /run. For every message it:
1. loads the earlier chat from memory,
2. starts the MCP servers,
3. runs the agent loop and streams each event back as it happens,
4. saves the chat and stops the MCP servers.

Start it with:  python -m uvicorn app.main:app
"""

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import config, memory
from app.agent import run_agent
from app.mcp_client import McpConnections

app = FastAPI(title="Coding Agent")


class AgentRequest(BaseModel):
    """What the client sends. FastAPI checks the types for us."""

    task: str
    target_repo: str
    session_id: str
    apply_changes: bool = False


async def agent_events(request: AgentRequest):
    """Run one message and yield every event (action, observation, done, ...)."""
    # An empty path would mean "the current folder", so reject it too.
    if not request.target_repo.strip() or not Path(request.target_repo).is_dir():
        yield {"type": "error", "message": f"Folder not found: {request.target_repo}"}
        return

    messages = memory.load_messages(request.session_id)
    mcp = McpConnections()
    await mcp.start(config.MCP_SERVERS)
    for error in mcp.errors:
        yield {"type": "warning", "message": error}

    try:
        async for event in run_agent(
            request.task, request.target_repo, request.apply_changes, messages, mcp=mcp
        ):
            yield event
        # Save only when the run finished. After an error the chat stays as it
        # was before this message, so a half-finished run never breaks memory.
        memory.save_messages(request.session_id, messages)
    except Exception as error:
        yield {"type": "error", "message": str(error)}
    finally:
        await mcp.stop()


async def server_sent_events(request: AgentRequest):
    """Turn each event into one "data: {...}" line. This format is called Server-Sent Events."""
    async for event in agent_events(request):
        yield f"data: {json.dumps(event)}\n\n"


@app.post("/run")
async def run(request: AgentRequest):
    return StreamingResponse(server_sent_events(request), media_type="text/event-stream")
