"""The agent loop: the heart of the project.

How it works:
1. Send the conversation + the list of tools to the model.
2. If the model replies with text only -> it is finished.
3. If the model asks for tools -> run them, add the results to the
   conversation, and go back to step 1.
4. Stop after config.MAX_STEPS rounds so it can never loop forever.

run_agent() is a *generator*: it `yield`s small event dicts while it works,
so the terminal can show each step live instead of waiting for the end.
"""

import json
from pathlib import Path

from openai import AsyncOpenAI

from app import config
from app.repo_summary import summarize_repo
from app.tools import run_tool, tool_descriptions

SYSTEM_PROMPT = """You are a coding assistant working inside one code repository.
Use the tools to look at files before answering. Never guess what a file contains.
To find where something is defined or used, use search_files instead of opening files one by one.
Only change files when the task asks for it, and change as little as possible.
You cannot delete files or run commands.
After changing code, call run_tests if it is available. If tests fail because of your
change, fix it and run the tests again.
When you are done, stop calling tools and reply with a short summary of what you did."""

READ_ONLY_NOTE = "\n\n(Changes are not allowed for this message: only look at files and suggest.)"


def make_client() -> AsyncOpenAI:
    """Connect to OpenRouter. It uses the same API as OpenAI, so we use the openai library."""
    if not config.OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY is missing. Add it to your .env file.")
    return AsyncOpenAI(api_key=config.OPENROUTER_API_KEY, base_url=config.OPENROUTER_BASE_URL)


def parse_arguments(text: str) -> dict:
    """The model sends tool arguments as JSON text. Turn it into a dict (or {} if broken)."""
    try:
        arguments = json.loads(text or "{}")
    except json.JSONDecodeError:
        return {}
    if isinstance(arguments, dict):
        return arguments
    return {}


def add_usage(totals: dict, response) -> None:
    """Add one model call's token counts (and price) to the running totals.

    Tokens are the pieces of text models read and write (about 4 characters
    each). Providers charge per token, so this is what the run costs.
    """
    totals["model_calls"] += 1
    usage = getattr(response, "usage", None)
    if usage is None:  # some providers (and our fake test model) send no usage data
        return
    totals["input_tokens"] += usage.prompt_tokens or 0  # what we sent: prompt + history
    totals["output_tokens"] += usage.completion_tokens or 0  # what the model wrote
    totals["cost"] += getattr(usage, "cost", None) or 0  # OpenRouter adds the price in dollars


async def run_agent(
    task: str, repo: str, apply_changes: bool, messages: list, client=None, mcp=None
):
    """Handle one user message. `messages` is the conversation so far and is updated in place.

    `mcp` is an optional McpConnections (see mcp_client.py) with extra tools.
    """
    client = client or make_client()
    tools = tool_descriptions(apply_changes)
    if mcp is not None:
        tools = tools + mcp.descriptions

    # First message of a conversation: start with our rules + a map of the code.
    if not messages:
        system_prompt = SYSTEM_PROMPT + "\n\n" + summarize_repo(Path(repo))
        messages.append({"role": "system", "content": system_prompt})
    if not apply_changes:
        task = task + READ_ONLY_NOTE
    messages.append({"role": "user", "content": task})
    usage = {"model_calls": 0, "input_tokens": 0, "output_tokens": 0, "cost": 0.0}

    for step in range(config.MAX_STEPS):
        # 1. Ask the model what to do next.
        response = await client.chat.completions.create(
            model=config.MODEL,
            messages=messages,
            tools=tools,
        )
        add_usage(usage, response)
        reply = response.choices[0].message

        # Save the model's reply in the conversation (as a plain dict).
        assistant_message = {"role": "assistant", "content": reply.content or ""}
        if reply.tool_calls:
            assistant_message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.function.name, "arguments": call.function.arguments},
                }
                for call in reply.tool_calls
            ]
        messages.append(assistant_message)

        # 2. No tool calls means the model has answered. We are done.
        if not reply.tool_calls:
            yield {"type": "done", "summary": reply.content or "(no answer)", "usage": usage}
            return

        # 3. Run every tool the model asked for and give it the results.
        for call in reply.tool_calls:
            name = call.function.name
            arguments = parse_arguments(call.function.arguments)     #parse_argument(we created this function above) is turning json sent by the model into a dict.
            yield {"type": "action", "tool": name, "arguments": arguments}

            if mcp is not None and name in mcp.tools:
                result = await mcp.call(name, arguments)  # tool from an MCP server
            else:
                result = run_tool(Path(repo), name, arguments, apply_changes)  # our file tools
            yield {"type": "observation", "tool": name, "result": result}

            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

    # 4. Safety limit reached.
    summary = f"Stopped after {config.MAX_STEPS} steps without finishing."
    yield {"type": "done", "summary": summary, "usage": usage}
