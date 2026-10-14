"""Run an async entry point (worker, jobs, CLI) with a loop psycopg can use everywhere."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable
from typing import Any

import anyio


def run(fn: Callable[..., Awaitable[Any]], *args: Any) -> Any:
    options: dict[str, Any] = {}
    if sys.platform == "win32":  # psycopg async can't use the Proactor loop
        options["loop_factory"] = asyncio.SelectorEventLoop
    return anyio.run(fn, *args, backend="asyncio", backend_options=options)
