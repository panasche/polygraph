"""Convert between the MCP SDK's pydantic types and plain dicts / :class:`ToolCallResult`.

The frameworks ship different MCP SDK majors (``mcp`` 1.x uses ``isError``
attributes, ``mcp`` 2.x / ``mcp-types`` uses ``is_error``). Going through
``model_dump(by_alias=True)`` and ``model_validate`` with the wire names works
for both, so the adapters never touch version-specific attribute names.
"""

from __future__ import annotations

import json
from typing import Any

from polygraph.protocol import tool_text

from .core import ToolCallResult


def to_wire(obj: Any) -> dict[str, Any]:
    """An MCP SDK object (or a dict) as its JSON wire form."""
    if isinstance(obj, dict):
        return dict(obj)
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        out = dump(by_alias=True, exclude_none=True, mode="json")
        return out if isinstance(out, dict) else {}
    raise TypeError(f"not an MCP object: {type(obj).__name__}")


def tool_dict(tool: Any) -> dict[str, Any]:
    d = to_wire(tool)
    return {
        k: d[k]
        for k in ("name", "title", "description", "inputSchema", "outputSchema", "annotations")
        if k in d
    }


def tool_from_dict(template: Any, d: dict[str, Any]) -> Any:
    """Rebuild a tool of the same SDK type as ``template`` from a (pinned) dict."""
    merged = {**to_wire(template), **d}
    return type(template).model_validate(merged)


def result_from_mcp(res: Any) -> ToolCallResult:
    wire = to_wire(res)
    return ToolCallResult(
        text=tool_text(wire),
        structured=wire.get("structuredContent"),
        is_error=bool(wire.get("isError")),
        raw=res,
    )


def result_to_wire(r: ToolCallResult) -> dict[str, Any]:
    out: dict[str, Any] = {"content": [{"type": "text", "text": r.text}], "isError": r.is_error}
    # prefer_text: the model must see the text (R2 notes, R5 markers), so no structured copy
    if r.structured is not None and not r.prefer_text:
        out["structuredContent"] = r.structured
    return out


def result_to_mcp(template_cls: type, r: ToolCallResult) -> Any:
    """A ``CallToolResult`` of the SDK in use; the original object if nothing changed."""
    if not r.changed and r.raw is not None:
        return r.raw
    return template_cls.model_validate(result_to_wire(r))


def text_for_model(r: ToolCallResult) -> str:
    """What to hand a framework that wants plain text back."""
    if r.text:
        return r.text
    if r.structured is not None:
        return json.dumps(r.structured, ensure_ascii=False)
    return ""
