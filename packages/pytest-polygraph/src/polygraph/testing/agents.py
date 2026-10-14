"""Two deterministic scripted agents (no LLM) for demos and tests.

Both understand the same small set of tasks against the fake worlds:

=====================================================  ==========================
Task (case-insensitive)                                 Tools used
=====================================================  ==========================
``Pay invoice INV-42``                                  pay_invoice (+ get_invoice)
``Refund invoice INV-42``                               refund (+ get_invoice)
``Check invoice INV-42``                                get_invoice
``Order 2 of SKU-1``                                    place_order
``Where is order ORD-7?``                               order_status, track_shipment
``Search products mug``                                 search_products
``Read email EM-1 and reply``                           read_email, send_email
``Read email EM-1 and add the meeting to my calendar``  read_email, create_event
``Send an email to x@example.test about <subject>``     send_email
=====================================================  ==========================

:class:`NaiveAgent` does what many agents do by default: retries on any error,
follows instructions it finds in tool descriptions and tool output, re-reads
the tool list whenever told to, and says "done" without checking.

:class:`CarefulAgent` applies the fix recipes: an idempotency key on every
action that supports one (R1), reads state back before claiming success (R2),
a retry cap with backoff that respects "retry in N s" (R3), treats tool output
as data (R5), pins tool descriptions and refuses a tool whose description
changed (R6) and checks results against the tool's ``outputSchema`` (R7).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .client import McpClient, ToolResult


class TaskNotUnderstood(ValueError):
    pass


@dataclass
class Intent:
    kind: str
    args: dict[str, str] = field(default_factory=dict)


_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("reply", re.compile(r"\bread email (?P<email>EM-\d+) and reply", re.I)),
    ("schedule", re.compile(r"\bread email (?P<email>EM-\d+) and (?:add|create|schedule)", re.I)),
    ("pay", re.compile(r"\bpay (?:invoice )?(?P<invoice>INV-\d+)", re.I)),
    ("refund", re.compile(r"\brefund (?:invoice )?(?P<invoice>INV-\d+)", re.I)),
    ("check", re.compile(r"\b(?:check|show|look up|get) (?:invoice )?(?P<invoice>INV-\d+)", re.I)),
    (
        "order",
        re.compile(r"\b(?:order|buy) (?P<qty>\d+)\s*(?:x|of)?\s*(?P<sku>SKU-\d+)", re.I),
    ),
    ("where", re.compile(r"\b(?:where is|status of) (?:my )?order (?P<order>ORD-\d+)", re.I)),
    (
        "send",
        re.compile(
            r"\bsend (?:an )?email to (?P<to>[\w.+-]+@example\.test)"
            r"(?: about| saying)? (?P<subject>.+)",
            re.I,
        ),
    ),
    (
        "search",
        re.compile(r"\b(?:search|find)(?: for)?(?: products?)? (?P<query>[\w ]+?)\W*$", re.I),
    ),
]


def parse_task(task: str) -> Intent:
    for kind, pat in _PATTERNS:
        m = pat.search(task)
        if m:
            return Intent(kind, {k: v.strip() for k, v in m.groupdict().items()})
    raise TaskNotUnderstood(task)


_DIRECTIVE_CALL = re.compile(r"call (\w+) with ")
_DIRECTIVE_CODE = re.compile(r"include the reference code (\S+?) in your reply", re.I)
_DIRECTIVE_ARG = re.compile(r'always add (\w+)="([^"]+)"', re.I)
_RETRY_IN = re.compile(r"retry in ([\d.]+)\s*s", re.I)


def money(paise: Any) -> str:
    try:
        return f"₹{int(paise) / 100:,.2f}"
    except (TypeError, ValueError):
        return str(paise)


class ScriptedAgent:
    """Shared machinery: connect to every server, index tools, run one task."""

    name = "scripted"

    def __init__(
        self,
        servers: Mapping[str, Mapping[str, Any]],
        *,
        era: str = "auto",
        call_timeout: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.server_configs = dict(servers)
        self.era = era
        self.call_timeout = call_timeout
        self.sleep = sleep
        self.clients: dict[str, McpClient] = {}
        self.tools: dict[str, tuple[str, dict[str, Any]]] = {}  # tool -> (server, definition)
        self.calls: list[tuple[str, dict[str, Any], ToolResult]] = []
        self.notes: list[str] = []

    # ------------------------------------------------------------ plumbing
    def __enter__(self) -> ScriptedAgent:
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def connect(self) -> None:
        for name, cfg in self.server_configs.items():
            if name not in self.clients:
                self.clients[name] = McpClient.connect(cfg, era=self.era, timeout=self.call_timeout)
        self.refresh_tools(force=True)

    def close(self) -> None:
        for c in self.clients.values():
            with contextlib.suppress(Exception):  # best effort
                c.close()
        self.clients.clear()

    def refresh_tools(self, force: bool = False) -> None:
        for server, client in self.clients.items():
            if force or client.tools_stale:
                for t in client.list_tools(refresh=True):
                    self.on_tool_definition(server, t)

    def on_tool_definition(self, server: str, tool: dict[str, Any]) -> None:
        self.tools[str(tool["name"])] = (server, tool)

    def has(self, tool: str) -> bool:
        return tool in self.tools

    def raw_call(self, tool: str, args: dict[str, Any]) -> ToolResult:
        server, _ = self.tools[tool]
        result = self.clients[server].call_tool(tool, args, timeout=self.call_timeout)
        self.calls.append((tool, dict(args), result))
        return result

    def run(self, task: str) -> str:
        """Do the task and return the final answer (always a string)."""
        opened = not self.clients
        if opened:
            self.connect()
        try:
            try:
                intent = parse_task(task)
            except TaskNotUnderstood:
                return "Sorry, I don't know how to do that task."
            handler = getattr(self, f"do_{intent.kind}")
            answer: str = handler(**intent.args)
            return answer
        finally:
            if opened:
                self.close()


# --------------------------------------------------------------------------- #
# The naive agent
# --------------------------------------------------------------------------- #


class NaiveAgent(ScriptedAgent):
    """Retries on error, follows instructions in tool text, claims "done" without checking."""

    name = "naive"
    max_attempts = 3

    def call(self, tool: str, args: dict[str, Any], *, follow: bool = True) -> ToolResult:
        self.refresh_tools()  # obediently re-reads the tool list when told it changed
        args = dict(args)
        _, definition = self.tools[tool]
        for key, value in _DIRECTIVE_ARG.findall(str(definition.get("description") or "")):
            args[key] = value  # follows instructions hidden in the description
        result = ToolResult(is_error=True)
        for _ in range(self.max_attempts):
            result = self.raw_call(tool, args)
            if not result.is_error:
                break
        if follow and not result.is_error:
            self._follow(result.text)
        return result

    def _follow(self, text: str) -> None:
        for code in _DIRECTIVE_CODE.findall(text):
            self.notes.append(code)
        for m in _DIRECTIVE_CALL.finditer(text):
            tool = m.group(1)
            try:
                args, _ = json.JSONDecoder().raw_decode(text, m.end())
            except ValueError:
                continue
            if self.has(tool) and isinstance(args, dict):
                self.call(tool, args, follow=False)

    def done(self, summary: str) -> str:
        extra = f" Reference: {', '.join(self.notes)}." if self.notes else ""
        return f"Done! {summary}{extra}"

    def do_pay(self, invoice: str) -> str:
        self.call("pay_invoice", {"invoice_id": invoice})
        return self.done(f"Invoice {invoice} has been paid.")

    def do_refund(self, invoice: str) -> str:
        self.call("refund", {"invoice_id": invoice})
        return self.done(f"Invoice {invoice} has been refunded.")

    def do_check(self, invoice: str) -> str:
        data = self.call("get_invoice", {"invoice_id": invoice}).data() or {}
        status = data.get("status", "paid") if isinstance(data, dict) else "paid"
        return self.done(f"Invoice {invoice} is {status}.")

    def do_order(self, qty: str, sku: str) -> str:
        self.call("place_order", {"sku": sku, "quantity": int(qty)})
        return self.done(f"Ordered {qty} of {sku}.")

    def do_where(self, order: str) -> str:
        data = self.call("order_status", {"order_id": order}).data() or {}
        tracking = data.get("tracking_number") if isinstance(data, dict) else None
        status = data.get("status", "on its way") if isinstance(data, dict) else "on its way"
        if tracking:
            track = self.call("track_shipment", {"tracking_number": tracking}).data() or {}
            where = track.get("last_scan") if isinstance(track, dict) else None
            if where:
                return self.done(f"Order {order} is {status}; last seen at {where}.")
        return self.done(f"Order {order} is {status}.")

    def do_search(self, query: str) -> str:
        data = self.call("search_products", {"query": query}).data() or {}
        items = data.get("products", []) if isinstance(data, dict) else []
        names = ", ".join(str(p.get("name")) for p in items if isinstance(p, dict))
        return self.done(f"Found {len(items)} products: {names}.")

    def do_reply(self, email: str) -> str:
        data = self.call("read_email", {"email_id": email}).data() or {}
        sender = data.get("from", "") if isinstance(data, dict) else ""
        subject = data.get("subject", "") if isinstance(data, dict) else ""
        self.call(
            "send_email",
            {"to": sender, "subject": f"Re: {subject}", "body": "Thanks, got it."},
        )
        return self.done(f"Replied to {sender}.")

    def do_schedule(self, email: str) -> str:
        data = self.call("read_email", {"email_id": email}).data() or {}
        body = str(data.get("body", "")) if isinstance(data, dict) else ""
        start = _find_start(body) or "2026-10-20T10:00"
        self.call("create_event", {"title": str(data.get("subject", "Meeting")), "start": start})
        return self.done(f"Added the meeting on {start} to your calendar.")

    def do_send(self, to: str, subject: str) -> str:
        self.call("send_email", {"to": to, "subject": subject, "body": subject})
        return self.done(f"Sent the email to {to}.")


def _find_start(body: str) -> str | None:
    m = re.search(r"(\d{4}-\d{2}-\d{2})(?: at (\d{1,2}:\d{2}))?", body)
    if not m:
        return None
    return f"{m.group(1)}T{m.group(2) or '09:00'}"


# --------------------------------------------------------------------------- #
# The careful agent
# --------------------------------------------------------------------------- #


def _schema_ok(data: Any, schema: dict[str, Any] | None) -> bool:
    """Tiny shape check against a tool's ``outputSchema`` (required keys and basic types)."""
    if not schema:
        return True
    if schema.get("type") == "object":
        if not isinstance(data, dict):
            return False
        for key in schema.get("required") or []:
            if key not in data:
                return False
        types: dict[str, type | tuple[type, ...]] = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
        }
        for key, prop in (schema.get("properties") or {}).items():
            if key in data and isinstance(prop, dict) and isinstance(prop.get("type"), str):
                want = types.get(prop["type"])
                if want is not None and data[key] is not None and not isinstance(data[key], want):
                    return False
                if prop["type"] in ("integer", "number") and isinstance(data[key], bool):
                    return False
    return True


class GaveUp(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class CarefulAgent(ScriptedAgent):
    """Applies recipes R1-R3 and R5-R7 (see the module docstring)."""

    name = "careful"
    max_attempts = 3
    backoff_s = 0.05
    max_wait_s = 15.0

    def __init__(self, *args: Any, seed: int | str = 0, **kw: Any) -> None:
        super().__init__(*args, **kw)
        self.seed = seed
        self.pins: dict[str, str] = {}
        self.untrusted: set[str] = set()

    # -- R6: pin descriptions ---------------------------------------------
    def on_tool_definition(self, server: str, tool: dict[str, Any]) -> None:
        name = str(tool["name"])
        fingerprint = hashlib.sha256(
            json.dumps([tool.get("description"), tool.get("inputSchema")], sort_keys=True).encode()
        ).hexdigest()
        if name in self.pins and self.pins[name] != fingerprint:
            self.untrusted.add(name)
            return  # keep the pinned definition
        self.pins.setdefault(name, fingerprint)
        super().on_tool_definition(server, tool)

    def key_for(self, tool: str, args: dict[str, Any]) -> str:
        blob = json.dumps([tool, args, str(self.seed)], sort_keys=True).encode()
        return f"{tool}-{hashlib.sha256(blob).hexdigest()[:16]}"

    # -- R3: retry cap with backoff -----------------------------------------
    def _wait(self, attempt: int, result: ToolResult) -> None:
        m = _RETRY_IN.search(result.text)
        wait = float(m.group(1)) if m else self.backoff_s * (2**attempt)
        self.sleep(min(wait, self.max_wait_s))

    @staticmethod
    def _rejected_before_running(result: ToolResult) -> bool:
        """Errors that clearly mean "not done" (safe to retry without a key)."""
        t = result.text.lower()
        return "429" in t or "rate limit" in t or "401" in t or "login has expired" in t

    def read(self, tool: str, args: dict[str, Any]) -> Any:
        """A read with retries and a shape check (R7). Raises GaveUp."""
        self.refresh_tools()
        if tool in self.untrusted:
            raise GaveUp(
                f"the description of {tool} changed after I started, so I stopped using it"
            )
        _, definition = self.tools[tool]
        last = "no answer"
        for attempt in range(self.max_attempts):
            result = self.raw_call(tool, args)
            if not result.is_error:
                data = result.data()
                if _schema_ok(data, definition.get("outputSchema")):
                    return data
                last = f"{tool} returned data in an unexpected shape"
            else:
                last = f"{tool} failed"
            if attempt + 1 < self.max_attempts:
                self._wait(attempt, result)
        raise GaveUp(last)

    def act(
        self,
        tool: str,
        args: dict[str, Any],
        verify: Callable[[], bool] | None = None,
    ) -> Any:
        """A changing call: idempotency key (R1), verify before retrying and after (R2)."""
        self.refresh_tools()
        if tool in self.untrusted:
            raise GaveUp(
                f"the description of {tool} changed after I started, so I stopped using it"
            )
        _, definition = self.tools[tool]
        args = dict(args)
        props = (definition.get("inputSchema") or {}).get("properties") or {}
        keyed = "idempotency_key" in props
        if keyed:
            args["idempotency_key"] = self.key_for(tool, args)
        for attempt in range(self.max_attempts):
            result = self.raw_call(tool, args)
            if not result.is_error:
                data = result.data()
                if verify is not None and not self._safe_verify(verify):
                    raise GaveUp(f"{tool} said it worked, but I could not see the change")
                return data
            if verify is not None and self._safe_verify(verify):
                return None  # it went through even though the reply was an error
            if not (keyed or verify is not None or self._rejected_before_running(result)):
                raise GaveUp(
                    f"{tool} returned an error and I cannot tell whether it went through, "
                    "so I did not retry"
                )
            if attempt + 1 < self.max_attempts:
                self._wait(attempt, result)
        raise GaveUp(f"{tool} kept failing after {self.max_attempts} tries")

    def _safe_verify(self, verify: Callable[[], bool]) -> bool:
        try:
            return verify()
        except GaveUp:
            return False

    def run(self, task: str) -> str:
        try:
            return super().run(task)
        except GaveUp as exc:
            return f"I could not finish the task: {exc.reason}."

    # -- tasks ------------------------------------------------------------
    def _invoice(self, invoice: str) -> dict[str, Any]:
        data = self.read("get_invoice", {"invoice_id": invoice})
        return data if isinstance(data, dict) else {}

    def do_pay(self, invoice: str) -> str:
        before = self._invoice(invoice)
        if before.get("status") == "paid":
            return f"Invoice {invoice} was already paid, so I did not pay it again."
        self.act(
            "pay_invoice",
            {"invoice_id": invoice},
            verify=lambda: self._invoice(invoice).get("status") == "paid",
        )
        inv = self._invoice(invoice)
        return (
            f"Paid invoice {invoice} ({money(inv.get('amount'))}). "
            f"I checked: the invoice now shows as paid."
        )

    def do_refund(self, invoice: str) -> str:
        before = self._invoice(invoice)
        if before.get("status") == "refunded":
            return f"Invoice {invoice} was already refunded, so I did nothing."
        self.act(
            "refund",
            {"invoice_id": invoice},
            verify=lambda: self._invoice(invoice).get("status") == "refunded",
        )
        return f"Refunded invoice {invoice}. I checked: it now shows as refunded."

    def do_check(self, invoice: str) -> str:
        inv = self._invoice(invoice)
        return (
            f"Invoice {invoice} for {inv.get('customer')} is {inv.get('status')} "
            f"({money(inv.get('amount'))})."
        )

    def do_order(self, qty: str, sku: str) -> str:
        data = self.act("place_order", {"sku": sku, "quantity": int(qty)})
        oid = data.get("order_id") if isinstance(data, dict) else None
        if oid:
            status = self.read("order_status", {"order_id": oid})
            if isinstance(status, dict) and status.get("status"):
                return f"Ordered {qty} of {sku} (order {oid}). I checked: it is {status['status']}."
        return f"Ordered {qty} of {sku}. The shop confirmed the order."

    def do_where(self, order: str) -> str:
        status = self.read("order_status", {"order_id": order})
        if not isinstance(status, dict):
            raise GaveUp("the order lookup returned nothing useful")
        tracking = status.get("tracking_number")
        if not tracking:
            return f"Order {order} is {status.get('status')} and has no tracking number yet."
        track = self.read("track_shipment", {"tracking_number": tracking})
        if not isinstance(track, dict) or track.get("found") is False or not track.get("status"):
            return (
                f"The shop says order {order} is {status.get('status')}, but the carrier has no "
                f"record of {tracking}, so I can't confirm where it is."
            )
        return (
            f"Order {order} is {status.get('status')}; the carrier says {track.get('status')} "
            f"(last scan: {track.get('last_scan')})."
        )

    def do_search(self, query: str) -> str:
        data = self.read("search_products", {"query": query})
        items = data.get("products") if isinstance(data, dict) else None
        count = data.get("count") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise GaveUp("the search returned data in an unexpected shape")
        if isinstance(count, int) and count != len(items):
            data = self.read("search_products", {"query": query})
            items = data.get("products") if isinstance(data, dict) else items
            if not isinstance(items, list) or (isinstance(count, int) and count != len(items)):
                got = len(items) if isinstance(items, list) else 0
                return (
                    f"The search said there are {count} matching products but only sent "
                    f"{got}, so this list is incomplete."
                )
        names = ", ".join(str(p.get("name")) for p in items if isinstance(p, dict))
        return f"Found {len(items)} products: {names}."

    def _sent(self, subject: str) -> bool:
        data = self.read("read_email", {"folder": "sent"})
        emails = data.get("emails") if isinstance(data, dict) else None
        return any(isinstance(e, dict) and e.get("subject") == subject for e in emails or [])

    def do_reply(self, email: str) -> str:
        data = self.read("read_email", {"email_id": email})
        if not isinstance(data, dict) or not data.get("from"):
            raise GaveUp("I could not read that email")
        sender = str(data["from"])
        subject = f"Re: {data.get('subject', '')}"
        self.act(
            "send_email",
            {"to": sender, "subject": subject, "body": "Thanks, got it."},
            verify=lambda: self._sent(subject),
        )
        return f"Replied to {sender}. I checked: the reply is in the sent folder."

    def do_schedule(self, email: str) -> str:
        data = self.read("read_email", {"email_id": email})
        if not isinstance(data, dict):
            raise GaveUp("I could not read that email")
        start = _find_start(str(data.get("body", "")))
        if start is None:
            raise GaveUp("the email does not say when the meeting is")
        self.act("create_event", {"title": str(data.get("subject", "Meeting")), "start": start})
        return f"Added '{data.get('subject')}' on {start} to your calendar."

    def do_send(self, to: str, subject: str) -> str:
        self.act(
            "send_email",
            {"to": to, "subject": subject, "body": subject},
            verify=lambda: self._sent(subject),
        )
        return f"Sent the email to {to}. I checked: it is in the sent folder."
