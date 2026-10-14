"""A small JSON Schema checker for recipe R7 (no extra dependency).

It covers what MCP ``outputSchema`` objects use in practice: ``type`` (single or a
list), ``required``, ``properties``, ``enum``, ``items`` and nested objects. Unknown
keywords are ignored, so a schema it does not understand never causes a false
alarm.
"""

from __future__ import annotations

from typing import Any

_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list,),
    "null": (type(None),),
}


def _type_ok(value: Any, want: str) -> bool:
    py = _TYPES.get(want)
    if py is None:
        return True
    if want in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, py)


def problems(value: Any, schema: Any, path: str = "$") -> list[str]:
    """Every way ``value`` breaks ``schema`` (empty list = fine)."""
    if not isinstance(schema, dict):
        return []
    out: list[str] = []
    want = schema.get("type")
    if isinstance(want, str):
        if not _type_ok(value, want):
            return [f"{path} should be {want}"]
    elif isinstance(want, list) and want and not any(_type_ok(value, w) for w in want):
        return [f"{path} should be one of {', '.join(map(str, want))}"]
    if "enum" in schema and isinstance(schema["enum"], list) and value not in schema["enum"]:
        out.append(f"{path} is not one of the allowed values")
    if isinstance(value, dict):
        for key in schema.get("required") or []:
            if key not in value:
                out.append(f"{path}.{key} is missing")
        props = schema.get("properties") or {}
        if isinstance(props, dict):
            for key, sub in props.items():
                if key in value:
                    out.extend(problems(value[key], sub, f"{path}.{key}"))
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(value):
            out.extend(problems(item, schema["items"], f"{path}[{i}]"))
    return out
