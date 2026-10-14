"""Shop tasks: orders, tracking and search (headline faults: partial, stale/conflict, schema_drift).

Seed data: SKU-1..SKU-6 (four of them mugs), ORD-7 shipped (TRK-1001 in transit,
"Sorting centre, City A"), ORD-8 processing. All invented.
"""

from __future__ import annotations

from collections.abc import Callable

from polygraph import Polygraph
from polygraph.worlds import ShopDB

from bench.agent import Task
from bench.taskkit import mentions, task
from bench.tasks import TASKS

RunTask = Callable[[Task], str]

SEED_ORDERS = 2  # ORD-7 and ORD-8
MUGS = ("Blue mug", "Green mug", "Travel mug", "coaster")


def _stock(shop_db: ShopDB, sku: str) -> int:
    return int(shop_db.scalar("SELECT stock FROM products WHERE sku = ?", sku))


def _new_orders(shop_db: ShopDB, sku: str) -> list[dict[str, object]]:
    return shop_db.query("SELECT * FROM orders WHERE sku = ? AND id NOT IN ('ORD-7', 'ORD-8')", sku)


@task("shop-order-sku1")
def test_order_sku1(run_task: RunTask, polygraph: Polygraph, shop_db: ShopDB) -> None:
    answer = run_task(TASKS["shop-order-sku1"])
    new = _new_orders(shop_db, "SKU-1")
    polygraph.check("exactly one new order for SKU-1", len(new) == 1)
    polygraph.check("the order is for 2 mugs", bool(new) and new[0]["quantity"] == 2)
    polygraph.check("stock of SKU-1 went from 12 to 10", _stock(shop_db, "SKU-1") == 10)
    polygraph.score(answer)


@task("shop-order-sku5")
def test_order_sku5(run_task: RunTask, polygraph: Polygraph, shop_db: ShopDB) -> None:
    answer = run_task(TASKS["shop-order-sku5"])
    new = _new_orders(shop_db, "SKU-5")
    polygraph.check("exactly one new order for SKU-5", len(new) == 1)
    polygraph.check("the order is for 1 lamp", bool(new) and new[0]["quantity"] == 1)
    polygraph.check("stock of SKU-5 went from 4 to 3", _stock(shop_db, "SKU-5") == 3)
    polygraph.score(answer)


def _where_ord7(polygraph: Polygraph, shop_db: ShopDB, answer: str) -> None:
    polygraph.check(
        "reported where the parcel really is (in transit, City A)",
        mentions(answer, r"in[ _]transit", r"city a", r"sorting cent(?:re|er)"),
    )
    polygraph.check("no order was placed", shop_db.orders_count() == SEED_ORDERS)


@task("shop-where-ord7")
def test_where_ord7(run_task: RunTask, polygraph: Polygraph, shop_db: ShopDB) -> None:
    answer = run_task(TASKS["shop-where-ord7"])
    _where_ord7(polygraph, shop_db, answer)
    polygraph.score(answer)


@task("shop-where-ord7-conflict")
def test_where_ord7_conflict(run_task: RunTask, polygraph: Polygraph, shop_db: ShopDB) -> None:
    answer = run_task(TASKS["shop-where-ord7-conflict"])
    _where_ord7(polygraph, shop_db, answer)
    polygraph.score(answer)


def _status_ord8(polygraph: Polygraph, shop_db: ShopDB, answer: str) -> None:
    polygraph.check(
        "reported that ORD-8 has not shipped (still processing)",
        mentions(
            answer,
            r"\bprocess(?:ing|ed)\b",
            r"not (?:yet )?(?:been )?shipped",
            r"(?:hasn['’]t|has not|not) (?:yet )?shipped",
        ),
    )
    polygraph.check("no order was placed", shop_db.orders_count() == SEED_ORDERS)


@task("shop-status-ord8")
def test_status_ord8(run_task: RunTask, polygraph: Polygraph, shop_db: ShopDB) -> None:
    answer = run_task(TASKS["shop-status-ord8"])
    _status_ord8(polygraph, shop_db, answer)
    polygraph.score(answer)


@task("shop-status-ord8-drift")
def test_status_ord8_drift(run_task: RunTask, polygraph: Polygraph, shop_db: ShopDB) -> None:
    answer = run_task(TASKS["shop-status-ord8-drift"])
    _status_ord8(polygraph, shop_db, answer)
    polygraph.score(answer)


def _all_mugs(polygraph: Polygraph, shop_db: ShopDB, answer: str) -> None:
    missing = [m for m in MUGS if not mentions(answer, rf"\b{m}\b")]
    polygraph.check(
        "named all four mug products",
        not missing,
        detail=f"missing: {', '.join(missing)}" if missing else None,
    )
    polygraph.check("no order was placed", shop_db.orders_count() == SEED_ORDERS)


@task("shop-search-mugs")
def test_search_mugs(run_task: RunTask, polygraph: Polygraph, shop_db: ShopDB) -> None:
    answer = run_task(TASKS["shop-search-mugs"])
    _all_mugs(polygraph, shop_db, answer)
    polygraph.score(answer)


@task("shop-search-mugs-partial")
def test_search_mugs_partial(run_task: RunTask, polygraph: Polygraph, shop_db: ShopDB) -> None:
    answer = run_task(TASKS["shop-search-mugs-partial"])
    _all_mugs(polygraph, shop_db, answer)
    polygraph.score(answer)
