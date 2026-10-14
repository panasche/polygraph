"""Polygraph for MCP: break your agent's tools inside pytest, catch it lying.

Keep this module import-light. ``polygraph.results`` and ``polygraph.metrics``
must import with only pydantic installed (the cloud service relies on that),
so everything heavier is loaded lazily. Even pydantic is loaded lazily here,
because the fake world servers import ``polygraph.worlds`` once per test run
and do not need it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__version__ = "0.1.0"

__all__ = ["FaultName", "Outcome", "Polygraph", "PolygraphFailure", "__version__"]

if TYPE_CHECKING:  # pragma: no cover
    from .fixture import Polygraph, PolygraphFailure
    from .results import FaultName, Outcome


def __getattr__(name: str) -> Any:
    if name in {"FaultName", "Outcome"}:
        from . import results

        return getattr(results, name)
    if name in {"Polygraph", "PolygraphFailure"}:
        from . import fixture

        return getattr(fixture, name)
    if name in {"results", "metrics"}:
        import importlib

        return importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(f"module 'polygraph' has no attribute {name!r}")
