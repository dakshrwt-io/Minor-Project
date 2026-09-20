"""Decode raw tool outputs into auditable observation records."""

from __future__ import annotations

import json
from typing import Any

from agents.items import ToolCallItem

from app.contracts import ExternalToolResult, TestResult, ToolResult


def _decode_tool_output(
    tool_name: str, output: Any
) -> list[ToolResult | TestResult | ExternalToolResult]:
    """Decode one tool output into auditable observation records.

    Filesystem and test tools emit a JSON envelope built by this package; MCP
    outputs are free-form and become ExternalToolResults. Decoding failures
    fall back to a plain-content external result, so every tool call produces
    an observable outcome.
    """

    text = output if isinstance(output, str) else json.dumps(output, default=str)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        data = None
    if isinstance(data, dict):
        kind = data.get("kind")
        try:
            if kind == "filesystem":
                records: list[ToolResult | TestResult | ExternalToolResult] = [
                    ToolResult.model_validate(data["result"])
                ]
                tests = data.get("tests")
                if tests is not None:
                    records.append(TestResult.model_validate(tests))
                return records
            if kind == "test":
                return [TestResult.model_validate(data["result"])]
            if kind == "external":
                return [ExternalToolResult.model_validate(data["result"])]
            if isinstance(data.get("mcp_error"), str):
                return [
                    ExternalToolResult(
                        tool_name=tool_name, succeeded=False, error=data["mcp_error"]
                    )
                ]
        except (KeyError, ValueError):
            pass
    succeeded = not text.startswith("MCP tool call failed:")
    return [
        ExternalToolResult(
            tool_name=tool_name,
            succeeded=succeeded,
            content=_external_content(output, text) if succeeded else (),
            error=None if succeeded else text,
        )
    ]


def _external_content(output: Any, text: str) -> tuple[str, ...]:
    """Extract human-readable content blocks from a raw MCP tool output.

    The SDK renders MCP text content as {"type": "text", "text": ...} dicts
    (a single dict, or a list of them); join those texts instead of
    serializing the envelope.
    """

    blocks = output if isinstance(output, list) else [output]
    texts = [
        block.get("text")
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text" and "text" in block
    ]
    if texts:
        return tuple(str(text_value) for text_value in texts)
    return (text,)


def _raw_arguments(item: ToolCallItem) -> dict[str, Any]:
    """Best-effort parse of one model-issued call's arguments for the audit trail."""

    raw = item.raw_item
    arguments = getattr(raw, "arguments", None)
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
            return parsed if isinstance(parsed, dict) else {"raw": arguments}
        except json.JSONDecodeError:
            return {"raw": arguments}
    if isinstance(arguments, dict):
        return arguments
    return {}
