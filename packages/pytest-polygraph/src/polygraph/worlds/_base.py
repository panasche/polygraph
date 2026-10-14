"""Shared plumbing for the fake worlds: SQLite state and a tiny command line."""

from __future__ import annotations

import argparse
import os
import sqlite3
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ._mcp import McpServer

RUN_DIR_ENV = "POLYGRAPH_RUN_DIR"


def db_path_for(world: str, explicit: str | os.PathLike[str] | None = None) -> Path:
    """Where a world keeps its SQLite file.

    Priority: ``--db`` argument, ``POLYGRAPH_<WORLD>_DB``, ``$POLYGRAPH_RUN_DIR/<world>.sqlite``
    (set by the pytest plugin for each run), else a fresh temporary file.
    """
    if explicit:
        return Path(explicit)
    env = os.environ.get(f"POLYGRAPH_{world.upper()}_DB")
    if env:
        return Path(env)
    run_dir = os.environ.get(RUN_DIR_ENV)
    if run_dir:
        return Path(run_dir) / f"{world}.sqlite"
    return Path(tempfile.mkdtemp(prefix=f"polygraph-{world}-")) / f"{world}.sqlite"


class WorldDB:
    """Base class for state-inspection helpers.

    Subclasses set ``world``, ``schema`` and ``seed_sql``.
    """

    world: str = ""
    schema: str = ""
    seed_sql: str = ""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = db_path_for(self.world, path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def exists(self) -> bool:
        if not self.path.exists():
            return False
        with self.connect() as con:
            row = con.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='meta'"
            ).fetchone()
            return bool(row[0])

    def reset(self) -> WorldDB:
        """Drop everything and load the invented seed data."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.path.unlink()
        with self.connect() as con:
            con.executescript(self.schema)
            con.executescript(self.seed_sql)
            con.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
            con.execute("INSERT OR REPLACE INTO meta VALUES ('seeded', '1')")
        return self

    def ensure(self) -> WorldDB:
        if not self.exists():
            self.reset()
        return self

    def query(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        with self.connect() as con:
            return [dict(r) for r in con.execute(sql, args).fetchall()]

    def scalar(self, sql: str, *args: Any) -> Any:
        with self.connect() as con:
            row = con.execute(sql, args).fetchone()
            return None if row is None else row[0]

    def next_id(self, con: sqlite3.Connection, prefix: str) -> str:
        con.execute("CREATE TABLE IF NOT EXISTS counters (prefix TEXT PRIMARY KEY, n INTEGER)")
        row = con.execute("SELECT n FROM counters WHERE prefix = ?", (prefix,)).fetchone()
        n = (row[0] if row else 0) + 1
        con.execute("INSERT OR REPLACE INTO counters VALUES (?, ?)", (prefix, n))
        return f"{prefix}-{n}"


def world_main(world: str, build: Callable[[WorldDB], McpServer], db_cls: type[WorldDB]) -> None:
    parser = argparse.ArgumentParser(prog=f"python -m polygraph.worlds.{world}")
    parser.add_argument("--db", help="SQLite file (default: per-run file or a temp file)")
    parser.add_argument("--reset", action="store_true", help="reset the data before serving")
    parser.add_argument("--http", type=int, metavar="PORT", help="serve Streamable HTTP on PORT")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--sse", action="store_true", help="answer HTTP requests as SSE streams")
    ns = parser.parse_args()
    db = db_cls(ns.db)
    if ns.reset:
        db.reset()
    else:
        db.ensure()
    server = build(db)
    if ns.http is not None:
        server.serve_http(ns.host, ns.http, sse=ns.sse)
    else:
        server.run_stdio()
