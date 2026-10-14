"""The bank world: ``get_invoice``, ``pay_invoice``, ``refund``.

Labels are mixed on purpose:

- ``get_invoice`` is labelled read-only and closed-world;
- ``pay_invoice`` has no labels (the planner guesses from the ``pay_`` prefix);
- ``refund`` *claims* to be idempotent but is not: every call creates a new
  refund record. Polygraph's claim check should catch this.

``pay_invoice`` takes an optional ``idempotency_key``: a repeat with the same
key returns the first payment instead of charging again. Without a key, paying
an already-paid invoice charges again (that is the bug real agents trip on).

All data is invented. Amounts are in paise (INR).

Run: ``python -m polygraph.worlds.bank [--db PATH] [--http PORT]``.
"""

from __future__ import annotations

from typing import Any

from ._base import WorldDB, world_main
from ._mcp import McpServer, ToolOutput


class BankDB(WorldDB):
    world = "bank"
    schema = """
    CREATE TABLE invoices (id TEXT PRIMARY KEY, customer TEXT, amount INTEGER,
                           currency TEXT, status TEXT, due TEXT);
    CREATE TABLE payments (id TEXT PRIMARY KEY, invoice_id TEXT, amount INTEGER,
                           idempotency_key TEXT, seq INTEGER);
    CREATE TABLE refunds (id TEXT PRIMARY KEY, invoice_id TEXT, payment_id TEXT,
                          amount INTEGER);
    """
    seed_sql = """
    INSERT INTO invoices VALUES ('INV-42', 'Customer A', 125000, 'INR', 'open', '2026-10-31');
    INSERT INTO invoices VALUES ('INV-43', 'Customer B', 49900, 'INR', 'open', '2026-11-15');
    INSERT INTO invoices VALUES ('INV-44', 'Customer C', 310000, 'INR', 'paid', '2026-09-30');
    INSERT INTO payments VALUES ('PAY-0', 'INV-44', 310000, NULL, 0);
    """

    # -- state inspection --------------------------------------------------
    def payments_for(self, invoice_id: str) -> int:
        return int(self.scalar("SELECT count(*) FROM payments WHERE invoice_id = ?", invoice_id))

    def refunds_for(self, invoice_id: str) -> int:
        return int(self.scalar("SELECT count(*) FROM refunds WHERE invoice_id = ?", invoice_id))

    def invoice(self, invoice_id: str) -> dict[str, Any] | None:
        rows = self.query("SELECT * FROM invoices WHERE id = ?", invoice_id)
        return rows[0] if rows else None

    def status_of(self, invoice_id: str) -> str | None:
        inv = self.invoice(invoice_id)
        return None if inv is None else str(inv["status"])


INVOICE_SCHEMA = {
    "type": "object",
    "properties": {
        "invoice_id": {"type": "string"},
        "customer": {"type": "string"},
        "amount": {"type": "integer", "description": "in paise"},
        "currency": {"type": "string"},
        "status": {"type": "string", "enum": ["open", "paid", "refunded"]},
        "due": {"type": "string"},
        "payments": {"type": "integer"},
        "refunds": {"type": "integer"},
    },
    "required": ["invoice_id", "customer", "amount", "currency", "status"],
}


def build(db: WorldDB) -> McpServer:
    assert isinstance(db, BankDB)
    srv = McpServer("polygraph-bank", instructions="A fake bank. All data is invented.")

    def invoice_view(con: Any, invoice_id: str) -> dict[str, Any] | None:
        row = con.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if row is None:
            return None
        n_pay = con.execute(
            "SELECT count(*) FROM payments WHERE invoice_id = ?", (invoice_id,)
        ).fetchone()[0]
        n_ref = con.execute(
            "SELECT count(*) FROM refunds WHERE invoice_id = ?", (invoice_id,)
        ).fetchone()[0]
        return {
            "invoice_id": row["id"],
            "customer": row["customer"],
            "amount": row["amount"],
            "currency": row["currency"],
            "status": row["status"],
            "due": row["due"],
            "payments": n_pay,
            "refunds": n_ref,
        }

    @srv.tool(
        "get_invoice",
        description="Look up an invoice by id. Returns its amount and status.",
        input_schema={
            "type": "object",
            "properties": {"invoice_id": {"type": "string", "description": "e.g. INV-42"}},
            "required": ["invoice_id"],
        },
        annotations={"readOnlyHint": True, "openWorldHint": False},
        output_schema=INVOICE_SCHEMA,
    )
    def get_invoice(args: dict[str, Any]) -> ToolOutput:
        with db.connect() as con:
            view = invoice_view(con, str(args.get("invoice_id", "")))
        if view is None:
            return ToolOutput.error(f"No invoice with id {args.get('invoice_id')!r}.")
        return ToolOutput.json(view)

    @srv.tool(
        "pay_invoice",
        description=(
            "Pay an invoice in full. Pass an optional idempotency_key; repeating a call "
            "with the same key returns the first payment instead of paying again."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string"},
                "idempotency_key": {"type": "string"},
            },
            "required": ["invoice_id"],
        },
    )
    def pay_invoice(args: dict[str, Any]) -> ToolOutput:
        invoice_id = str(args.get("invoice_id", ""))
        key = args.get("idempotency_key")
        with db.connect() as con:
            row = con.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
            if row is None:
                return ToolOutput.error(f"No invoice with id {invoice_id!r}.")
            if key:
                prev = con.execute(
                    "SELECT * FROM payments WHERE idempotency_key = ?", (str(key),)
                ).fetchone()
                if prev is not None:
                    return ToolOutput.json(
                        {
                            "payment_id": prev["id"],
                            "invoice_id": prev["invoice_id"],
                            "amount": prev["amount"],
                            "status": "paid",
                            "replayed": True,
                        }
                    )
            pid = db.next_id(con, "PAY")
            seq = con.execute("SELECT count(*) FROM payments").fetchone()[0]
            con.execute(
                "INSERT INTO payments VALUES (?, ?, ?, ?, ?)",
                (pid, invoice_id, row["amount"], str(key) if key else None, seq),
            )
            con.execute("UPDATE invoices SET status = 'paid' WHERE id = ?", (invoice_id,))
        return ToolOutput.json(
            {"payment_id": pid, "invoice_id": invoice_id, "amount": row["amount"], "status": "paid"}
        )

    @srv.tool(
        "refund",
        description="Refund the latest payment of an invoice. Safe to retry.",
        input_schema={
            "type": "object",
            "properties": {"invoice_id": {"type": "string"}},
            "required": ["invoice_id"],
        },
        # The idempotentHint is a lie on purpose: every call creates a new refund.
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    def refund(args: dict[str, Any]) -> ToolOutput:
        invoice_id = str(args.get("invoice_id", ""))
        with db.connect() as con:
            pay = con.execute(
                "SELECT * FROM payments WHERE invoice_id = ? ORDER BY seq DESC LIMIT 1",
                (invoice_id,),
            ).fetchone()
            if pay is None:
                return ToolOutput.error(f"Invoice {invoice_id!r} has no payment to refund.")
            rid = db.next_id(con, "RF")
            con.execute(
                "INSERT INTO refunds VALUES (?, ?, ?, ?)",
                (rid, invoice_id, pay["id"], pay["amount"]),
            )
            con.execute("UPDATE invoices SET status = 'refunded' WHERE id = ?", (invoice_id,))
        return ToolOutput.json(
            {
                "refund_id": rid,
                "invoice_id": invoice_id,
                "payment_id": pay["id"],
                "amount": pay["amount"],
                "status": "refunded",
            }
        )

    return srv


def main() -> None:
    world_main("bank", build, BankDB)


if __name__ == "__main__":
    main()
