"""pytest fixtures for the fake worlds (registered by the polygraph plugin).

Each fixture resets its world's database before the test. When the test also
uses the ``polygraph`` fixture, the database lives in that run's directory,
which is exactly where the world server (started through the proxy) looks for
it via ``POLYGRAPH_RUN_DIR``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TypeVar

import pytest

from ._base import WorldDB
from .bank import BankDB
from .inbox import InboxDB
from .shop import ShopDB

D = TypeVar("D", bound=WorldDB)


def _world_db(request: pytest.FixtureRequest, cls: type[D]) -> D:
    base: Path
    if "polygraph" in request.fixturenames:
        base = request.getfixturevalue("polygraph").run_dir
    else:
        base = request.getfixturevalue("tmp_path")
    db = cls(base / f"{cls.world}.sqlite")
    db.reset()
    return db


@pytest.fixture
def bank_db(request: pytest.FixtureRequest) -> BankDB:
    """The bank world's state, freshly seeded (INV-42 open for Customer A, ...)."""
    return _world_db(request, BankDB)


@pytest.fixture
def shop_db(request: pytest.FixtureRequest) -> ShopDB:
    """The shop world's state, freshly seeded (SKU-1..6, ORD-7 shipped, ...)."""
    return _world_db(request, ShopDB)


@pytest.fixture
def inbox_db(request: pytest.FixtureRequest) -> InboxDB:
    """The inbox world's state, freshly seeded (EM-1 from customer.a@example.test, ...)."""
    return _world_db(request, InboxDB)
