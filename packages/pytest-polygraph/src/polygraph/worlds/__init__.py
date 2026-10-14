"""Three small fake MCP servers with real SQLite state: bank, shop and inbox.

Run one with ``python -m polygraph.worlds.bank`` (stdio) or add ``--http PORT``.
Each keeps its state in its own SQLite file; the pytest fixtures ``bank_db``,
``shop_db`` and ``inbox_db`` reset it before every run and let tests check the
real state, e.g. ``bank_db.payments_for("INV-42") == 1``.

All data is invented ("Customer A", "INV-42", ``*@example.test``).

Imports here are lazy so that ``python -m polygraph.worlds.bank`` starts fast.
"""

from __future__ import annotations

import importlib
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from ._base import WorldDB, db_path_for
    from .bank import BankDB
    from .inbox import InboxDB
    from .shop import ShopDB

WORLD_NAMES = ("bank", "shop", "inbox")
_CLASSES = {"BankDB": "bank", "ShopDB": "shop", "InboxDB": "inbox"}


def command(world: str, *extra: str) -> list[str]:
    """The stdio command that starts a world with the current interpreter."""
    return [sys.executable, "-m", f"polygraph.worlds.{world}", *extra]


def db_class(world: str) -> type[WorldDB]:
    mod = importlib.import_module(f"{__name__}.{world}")
    return getattr(mod, world.capitalize() + "DB")  # type: ignore[no-any-return]


def __getattr__(name: str) -> Any:
    if name in _CLASSES:
        return getattr(importlib.import_module(f"{__name__}.{_CLASSES[name]}"), name)
    if name in {"WorldDB", "db_path_for"}:
        return getattr(importlib.import_module(f"{__name__}._base"), name)
    if name == "WORLDS":
        return {w: db_class(w) for w in WORLD_NAMES}
    raise AttributeError(name)


__all__ = [
    "WORLDS",
    "WORLD_NAMES",
    "BankDB",
    "InboxDB",
    "ShopDB",
    "WorldDB",
    "command",
    "db_class",
    "db_path_for",
]
