from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

pytest_plugins = ["pytester"]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
