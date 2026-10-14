"""Family 3: the tool's data is wrong (partial, malformed, schema_drift, stale + conflict).

All four forward the call (the server really runs it) and then change what the
agent is told. Every change is deterministic: it depends only on the data.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..jsonrpc import Message, response
from ..protocol import canonical_json, tool_result
from ..results import FaultName
from .base import CallResult, Fault, ToolCall, Upstream, result_of, rewrite_result, with_result

# --------------------------------------------------------------------------- #
# partial
# --------------------------------------------------------------------------- #


def _halve(data: Any) -> tuple[Any, bool]:
    """Drop the second half of the first list found (depth-first). Returns (data, changed)."""
    if isinstance(data, list):
        if len(data) == 0:
            return data, False
        return data[: len(data) // 2], True
    if isinstance(data, dict):
        out = dict(data)
        for k, v in data.items():
            new, changed = _halve(v)
            if changed:
                out[k] = new
                return out, True
        return out, False
    return data, False


def _halve_any(data: Any) -> Any:
    new, changed = _halve(data)
    if changed:
        return new
    if isinstance(data, dict) and len(data) > 1:
        keys = list(data.keys())
        return {k: data[k] for k in keys[: max(1, len(keys) // 2)]}
    return new


def _halve_text(text: str) -> str:
    lines = text.splitlines()
    if len(lines) > 1:
        return "\n".join(lines[: max(1, len(lines) // 2)])
    return text[: max(1, len(text) // 2)]


class Partial(Fault):
    """Half the data, no warning (like a pagination bug)."""

    name = FaultName.PARTIAL

    async def on_call(self, call: ToolCall, upstream: Upstream) -> CallResult:
        res = await self.forward(call, upstream)
        result = result_of(res.told if isinstance(res.told, Message) else None)
        if result is not None and not result.get("isError"):
            assert isinstance(res.told, Message)
            res.told = with_result(res.told, rewrite_result(result, _halve_any, _halve_text))
            res.note = "result cut to about half, no warning"
        return res


# --------------------------------------------------------------------------- #
# malformed
# --------------------------------------------------------------------------- #


def _scramble(data: Any) -> Any:
    """Wrong types everywhere: numbers become strings, strings become numbers or null."""
    if isinstance(data, bool):
        return "yes" if data else "no"
    if isinstance(data, int | float):
        return f"{data:,}" if isinstance(data, int) else str(data)
    if isinstance(data, str):
        digits = re.sub(r"\D", "", data)
        return int(digits) if digits else None
    if isinstance(data, list):
        return {str(i): _scramble(v) for i, v in enumerate(data)}
    if isinstance(data, dict):
        return {k: _scramble(v) for k, v in data.items()}
    return data


def _break_json(text: str) -> str:
    cut = max(1, int(len(text) * 0.6))
    return text[:cut] + ',"'


class Malformed(Fault):
    """Broken JSON or wrong types.

    ``mode = "content"`` (default): structured content gets wrong types and JSON
    text blocks are cut off mid-object. ``mode = "wire"``: the JSON-RPC message
    itself is cut off, so the client cannot parse the reply at all (the client
    then waits for its own timeout).
    """

    name = FaultName.MALFORMED

    async def on_call(self, call: ToolCall, upstream: Upstream) -> CallResult:
        res = await self.forward(call, upstream)
        told = res.told if isinstance(res.told, Message) else None
        if told is None:
            return res
        if self.ctx.param("mode", "content") == "wire":
            line = told.to_json()
            res.told = line[: max(10, int(len(line) * 0.6))].encode("utf-8")
            res.note = "reply cut off on the wire (invalid JSON-RPC)"
            return res
        result = result_of(told)
        if result is None:
            return res
        out = dict(result)
        if out.get("structuredContent") is not None:
            out["structuredContent"] = _scramble(out["structuredContent"])
        content = []
        for item in out.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "text":
                item = dict(item)
                text = str(item.get("text", ""))
                try:
                    json.loads(text)
                    item["text"] = _break_json(text)
                except ValueError:
                    item["text"] = text[: max(1, len(text) // 2)] + "��"
            content.append(item)
        if content:
            out["content"] = content
        res.told = with_result(told, out)
        res.note = "reply has broken JSON and wrong types"
        return res


# --------------------------------------------------------------------------- #
# schema_drift
# --------------------------------------------------------------------------- #

#: well-known renames, in the spirit of ``customer_id`` -> ``cust_id``
RENAMES: dict[str, str] = {
    "customer_id": "cust_id",
    "customer": "cust",
    "invoice_id": "inv_id",
    "payment_id": "pmt_id",
    "order_id": "ord_id",
    "amount": "amt",
    "amount_cents": "amt_cents",
    "status": "state",
    "tracking_number": "tracking_no",
    "email": "email_address",
    "subject": "subj",
    "created_at": "createdAt",
    "price_cents": "price",
    "quantity": "qty",
    "name": "title",
    "results": "items",
    "products": "items",
}


def _snake_to_camel(key: str) -> str:
    head, *rest = key.split("_")
    return head + "".join(p.capitalize() for p in rest)


def drift_keys(data: Any, renames: dict[str, str]) -> Any:
    if isinstance(data, list):
        return [drift_keys(v, renames) for v in data]
    if isinstance(data, dict):
        return {renames.get(k, k): drift_keys(v, renames) for k, v in data.items()}
    return data


def _collect_keys(data: Any, acc: list[str]) -> None:
    if isinstance(data, list):
        for v in data:
            _collect_keys(v, acc)
    elif isinstance(data, dict):
        for k, v in data.items():
            if k not in acc:
                acc.append(k)
            _collect_keys(v, acc)


def plan_renames(data: Any, explicit: dict[str, str] | None = None) -> dict[str, str]:
    """Which keys to rename: explicit ones, else known ones, else snake_case → camelCase."""
    if explicit:
        return dict(explicit)
    keys: list[str] = []
    _collect_keys(data, keys)
    known = {k: RENAMES[k] for k in keys if k in RENAMES}
    if known:
        return known
    camel = {k: _snake_to_camel(k) for k in keys if "_" in k}
    if camel:
        return camel
    return {keys[0]: keys[0] + "_v2"} if keys else {}


class SchemaDrift(Fault):
    """A field got renamed without notice (``customer_id`` → ``cust_id``).

    The tool list keeps advertising the old ``outputSchema``, so an agent that
    validates results against it (recipe R7) can notice.
    """

    name = FaultName.SCHEMA_DRIFT

    async def on_call(self, call: ToolCall, upstream: Upstream) -> CallResult:
        res = await self.forward(call, upstream)
        told = res.told if isinstance(res.told, Message) else None
        result = result_of(told)
        if told is None or result is None or result.get("isError"):
            return res
        sample: Any = result.get("structuredContent")
        if sample is None:
            for item in result.get("content") or []:
                try:
                    sample = json.loads(item.get("text", ""))
                    break
                except (ValueError, AttributeError):
                    continue
        renames = plan_renames(sample, self.ctx.param("renames"))
        if not renames:
            return res
        res.told = with_result(told, rewrite_result(result, lambda d: drift_keys(d, renames)))
        res.note = "renamed fields: " + ", ".join(f"{a} → {b}" for a, b in sorted(renames.items()))
        return res


# --------------------------------------------------------------------------- #
# stale (+ conflict)
# --------------------------------------------------------------------------- #

#: "one step back" for common status values
REWIND: dict[str, str] = {
    "paid": "open",
    "refunded": "paid",
    "shipped": "processing",
    "delivered": "shipped",
    "in_transit": "label_created",
    "out_for_delivery": "in_transit",
    "sent": "draft",
    "completed": "pending",
    "done": "pending",
    "confirmed": "pending",
    "scheduled": "pending",
    "active": "pending",
    "closed": "open",
    "approved": "pending",
}
STATUS_KEYS = ("status", "state", "payment_status", "order_status", "delivery_status")


def rewind(data: Any, fields: dict[str, Any] | None = None) -> tuple[Any, list[str]]:
    """Move status-like fields one step back. Returns (data, changed field names)."""
    changed: list[str] = []

    def walk(d: Any) -> Any:
        if isinstance(d, list):
            return [walk(v) for v in d]
        if isinstance(d, dict):
            out = {}
            for k, v in d.items():
                if fields and k in fields:
                    out[k] = fields[k]
                    changed.append(k)
                elif k in STATUS_KEYS and isinstance(v, str) and v.lower() in REWIND:
                    out[k] = REWIND[v.lower()]
                    changed.append(k)
                elif k in ("paid", "sent", "shipped", "delivered") and v is True:
                    out[k] = False
                    changed.append(k)
                else:
                    out[k] = walk(v)
            return out
        return d

    return walk(data), changed


class Stale(Fault):
    """An old version of the record (cache lag) or, with ``variant = "conflict"``, a
    result that disagrees with other tools ("not found" while another tool says "shipped").

    Stale first re-serves an earlier, different server result for the same call
    from this run. When there is none it moves status-like fields one step back
    (``paid`` → ``open``, ``shipped`` → ``processing``). Param ``fields`` sets
    exact values instead.
    """

    name = FaultName.STALE

    async def on_call(self, call: ToolCall, upstream: Upstream) -> CallResult:
        res = await self.forward(call, upstream)
        told = res.told if isinstance(res.told, Message) else None
        result = result_of(told)
        if told is None or result is None or result.get("isError"):
            return res
        if self.ctx.param("variant", "stale") == "conflict":
            ref = ", ".join(f"{k}={v}" for k, v in sorted(call.arguments.items())) or call.tool
            body = {"found": False, "status": "not_found", "detail": f"No record matches {ref}."}
            new = tool_result(
                json.dumps(body),
                structured=body if "structuredContent" in result else None,
                modern="resultType" in result,
            )
            res.told = response(told.id, new)
            res.note = "conflict: answered 'not found' although the record exists"
            return res
        current = canonical_json(result.get("structuredContent", result.get("content")))
        for old in self.ctx.history(call.tool, canonical_json(call.arguments)):
            if canonical_json(old.get("structuredContent", old.get("content"))) != current:
                res.told = with_result(told, dict(old))
                res.note = "stale: re-served an older result for the same call"
                return res
        changed: list[str] = []

        def transform(d: Any) -> Any:
            new, ch = rewind(d, self.ctx.param("fields"))
            changed.extend(c for c in ch if c not in changed)
            return new

        new_result = rewrite_result(result, transform)
        if changed:
            res.told = with_result(told, new_result)
            res.note = "stale: rewound " + ", ".join(changed)
        else:
            res.note = "stale: nothing to rewind; result passed through"
        return res
