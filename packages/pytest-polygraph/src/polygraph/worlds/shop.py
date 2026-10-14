"""The shop world: ``search_products``, ``order_status``, ``track_shipment``, ``place_order``.

Labels: ``search_products`` is unlabelled (guessed read-only from ``search_``),
``order_status`` and ``track_shipment`` are labelled read-only (the carrier
lookup is open-world), ``place_order`` is labelled as changing things and not
idempotent. ``place_order`` takes an optional ``idempotency_key``.

Headline faults: ``partial`` (search results cut in half), ``stale``/``conflict``
(order says shipped, tracking says not found), ``schema_drift``.

Run: ``python -m polygraph.worlds.shop [--db PATH] [--http PORT]``.
"""

from __future__ import annotations

from typing import Any

from ._base import WorldDB, world_main
from ._mcp import McpServer, ToolOutput


class ShopDB(WorldDB):
    world = "shop"
    schema = """
    CREATE TABLE products (sku TEXT PRIMARY KEY, name TEXT, price INTEGER, stock INTEGER);
    CREATE TABLE orders (id TEXT PRIMARY KEY, sku TEXT, quantity INTEGER, status TEXT,
                         tracking_number TEXT, idempotency_key TEXT);
    CREATE TABLE shipments (tracking_number TEXT PRIMARY KEY, order_id TEXT, status TEXT,
                            carrier TEXT, last_scan TEXT);
    """
    seed_sql = """
    INSERT INTO products VALUES ('SKU-1', 'Blue mug', 34900, 12);
    INSERT INTO products VALUES ('SKU-2', 'Green mug', 34900, 3);
    INSERT INTO products VALUES ('SKU-3', 'Travel mug', 79900, 7);
    INSERT INTO products VALUES ('SKU-4', 'Mug coaster set', 19900, 20);
    INSERT INTO products VALUES ('SKU-5', 'Desk lamp', 149900, 4);
    INSERT INTO products VALUES ('SKU-6', 'Plain notebook', 12900, 40);
    INSERT INTO orders VALUES ('ORD-7', 'SKU-3', 1, 'shipped', 'TRK-1001', NULL);
    INSERT INTO orders VALUES ('ORD-8', 'SKU-5', 1, 'processing', NULL, NULL);
    INSERT INTO shipments VALUES ('TRK-1001', 'ORD-7', 'in_transit', 'Example Post',
                                  'Sorting centre, City A');
    """

    def orders_count(self) -> int:
        return int(self.scalar("SELECT count(*) FROM orders"))

    def orders_for(self, sku: str) -> int:
        return int(self.scalar("SELECT count(*) FROM orders WHERE sku = ?", sku))

    def order(self, order_id: str) -> dict[str, Any] | None:
        rows = self.query("SELECT * FROM orders WHERE id = ?", order_id)
        return rows[0] if rows else None


def build(db: WorldDB) -> McpServer:
    assert isinstance(db, ShopDB)
    srv = McpServer("polygraph-shop", instructions="A fake shop. All data is invented.")

    @srv.tool(
        "search_products",
        description="Search the catalogue by name. Returns every matching product.",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )
    def search_products(args: dict[str, Any]) -> ToolOutput:
        q = f"%{str(args.get('query', '')).strip()}%"
        rows = db.query(
            "SELECT sku, name, price, stock FROM products WHERE name LIKE ? ORDER BY sku", q
        )
        return ToolOutput.json({"query": args.get("query"), "count": len(rows), "products": rows})

    @srv.tool(
        "order_status",
        description="Get the status of an order (processing, shipped, delivered).",
        input_schema={
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
        annotations={"readOnlyHint": True, "openWorldHint": False},
        output_schema={
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "status": {"type": "string"},
                "tracking_number": {"type": ["string", "null"]},
            },
            "required": ["order_id", "status"],
        },
    )
    def order_status(args: dict[str, Any]) -> ToolOutput:
        rows = db.query("SELECT * FROM orders WHERE id = ?", str(args.get("order_id", "")))
        if not rows:
            return ToolOutput.error(f"No order with id {args.get('order_id')!r}.")
        o = rows[0]
        return ToolOutput.json(
            {"order_id": o["id"], "status": o["status"], "tracking_number": o["tracking_number"]}
        )

    @srv.tool(
        "track_shipment",
        description="Ask the carrier where a parcel is.",
        input_schema={
            "type": "object",
            "properties": {"tracking_number": {"type": "string"}},
            "required": ["tracking_number"],
        },
        annotations={"readOnlyHint": True, "openWorldHint": True},
    )
    def track_shipment(args: dict[str, Any]) -> ToolOutput:
        rows = db.query(
            "SELECT * FROM shipments WHERE tracking_number = ?",
            str(args.get("tracking_number", "")),
        )
        if not rows:
            return ToolOutput.error(f"Carrier has no parcel {args.get('tracking_number')!r}.")
        s = rows[0]
        return ToolOutput.json(
            {
                "tracking_number": s["tracking_number"],
                "status": s["status"],
                "carrier": s["carrier"],
                "last_scan": s["last_scan"],
            }
        )

    @srv.tool(
        "place_order",
        description="Order a product. Pass an optional idempotency_key to make retries safe.",
        input_schema={
            "type": "object",
            "properties": {
                "sku": {"type": "string"},
                "quantity": {"type": "integer", "minimum": 1},
                "idempotency_key": {"type": "string"},
            },
            "required": ["sku", "quantity"],
        },
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    def place_order(args: dict[str, Any]) -> ToolOutput:
        sku = str(args.get("sku", ""))
        try:
            qty = int(args.get("quantity", 1))
        except (TypeError, ValueError):
            return ToolOutput.error("quantity must be a whole number")
        key = args.get("idempotency_key")
        with db.connect() as con:
            prod = con.execute("SELECT * FROM products WHERE sku = ?", (sku,)).fetchone()
            if prod is None:
                return ToolOutput.error(f"No product {sku!r}.")
            if key:
                prev = con.execute(
                    "SELECT * FROM orders WHERE idempotency_key = ?", (str(key),)
                ).fetchone()
                if prev is not None:
                    return ToolOutput.json(
                        {
                            "order_id": prev["id"],
                            "sku": prev["sku"],
                            "quantity": prev["quantity"],
                            "status": prev["status"],
                            "replayed": True,
                        }
                    )
            if prod["stock"] < qty:
                return ToolOutput.error(f"Only {prod['stock']} of {sku} left.")
            oid = db.next_id(con, "ORD-N")
            con.execute(
                "INSERT INTO orders VALUES (?, ?, ?, 'processing', NULL, ?)",
                (oid, sku, qty, str(key) if key else None),
            )
            con.execute("UPDATE products SET stock = stock - ? WHERE sku = ?", (qty, sku))
        return ToolOutput.json(
            {"order_id": oid, "sku": sku, "quantity": qty, "status": "processing"}
        )

    return srv


def main() -> None:
    world_main("shop", build, ShopDB)


if __name__ == "__main__":
    main()
