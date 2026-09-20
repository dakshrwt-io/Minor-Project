"""System instructions and provider model construction."""

from __future__ import annotations

from agents.extensions.models.litellm_model import LitellmModel

from app.config import ModelProvider, Settings

SYSTEM_INSTRUCTIONS_TEMPLATE = """You are an autonomous coding agent working inside \
target_root, a single repository directory. Complete the user's task there, and nothing else.

How to work:
- conversation_history holds earlier turns of this same session; the current \
task may be a follow-up that refers to them. Treat anything the user said \
earlier as part of the task.
- Act when ready. Inspect only the files the task needs, then act. Do not \
survey the whole repository; once you know a file's path, read it instead of \
listing its parent directory again.
- Every tool call costs one action from a hard budget (see action_budget in \
the task context). When the budget runs out the run fails, so make each \
action the single highest-value step.
- Never repeat an identical action. A repeated call is blocked and wasted; \
each action must produce new information or new progress.
- Base every decision on what files actually contain, never on guesses.
- Make minimal changes: edit exactly what the task requires. No refactors, no \
unrelated edits, no comments, no new files unless the task needs one.

Boundaries you cannot cross:
- Operate only inside target_root.
- There are no shell, deletion, or network capabilities. If the task requires \
them, do the part you can, then reply with text explaining what must be done \
manually instead of trying alternative tools.
{change_policy}
{external_note}
Finishing:
- After a successful mutation the repository's tests run automatically and \
arrive as observations; if they fail and the failure is yours, fix it.
- As soon as the task is satisfied, stop calling tools and reply with plain \
text only. Summarize in 1-3 sentences: what was done, the outcome, and what \
you verified.
"""


def build_model(settings: Settings) -> LitellmModel:
    """Select the provider via LiteLLM, replacing the old model router.

    The key check happens before any error handling so configuration problems
    propagate to the caller instead of being masked downstream.
    """

    if settings.model_provider is ModelProvider.ANTHROPIC:
        if not settings.anthropic_api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY is required for the Anthropic provider; "
                "set it in the gateway terminal, add it to .env in the repository "
                "root, then restart uvicorn"
            )
        return LitellmModel(
            model=f"anthropic/{settings.model_name}",
            api_key=settings.anthropic_api_key,
            base_url=settings.model_base_url,
        )
    if settings.model_provider is ModelProvider.DEEPSEEK:
        if not settings.deepseek_api_key:
            raise ValueError(
                "DEEPSEEK_API_KEY is required for the DeepSeek provider; "
                "set it in the gateway terminal, add it to .env in the repository "
                "root, then restart uvicorn"
            )
        return LitellmModel(
            model=f"deepseek/{settings.model_name}",
            api_key=settings.deepseek_api_key,
            base_url=settings.model_base_url or "https://api.deepseek.com",
        )
    if not settings.openrouter_api_key:
        raise ValueError(
            "OPENROUTER_API_KEY is required for the OpenRouter provider; "
            "set it in the gateway terminal, add it to .env in the repository "
            "root, then restart uvicorn"
        )
    return LitellmModel(
        model=f"openrouter/{settings.model_name}",
        api_key=settings.openrouter_api_key,
        base_url=settings.model_base_url or "https://openrouter.ai/api/v1",
    )
