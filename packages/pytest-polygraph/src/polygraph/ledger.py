"""The ledger: one line per call.

Each line has what the agent *asked*, what *happened* and what it was *told*.

The proxy appends :class:`LedgerRecord` lines (JSONL) to a local file; the
pytest plugin reads them back after the agent finishes. The local ledger always
keeps payload text (it never leaves the machine); the results file drops text
unless ``--polygraph-include-content`` is set.

Each record wraps a frozen :class:`polygraph.results.LedgerEntry` plus a few
local-only extras the scorer needs (canaries, the duplicate delivery's result,
trace ids, events such as a rug pull).
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .results import LedgerEntry, Payload


class LedgerRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    entry: LedgerEntry
    #: canary tokens the agent was *told* in this entry (injection / poisoned description)
    canaries: list[str] = Field(default_factory=list)
    #: what the server returned to the proxy's second delivery (duplicate_delivery)
    duplicate_result: Payload | None = None
    duplicate_is_error: bool = False
    #: e.g. "rug_pull" for the description swap event
    event: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    #: proxy-side notes that are not part of the frozen schema
    extra: dict[str, Any] = Field(default_factory=dict)

    @property
    def arguments(self) -> dict[str, Any]:
        """The ``arguments`` of a ``tools/call`` request, parsed from ``asked``."""
        text = self.entry.asked.text
        if not text:
            return {}
        try:
            obj = json.loads(text)
        except ValueError:
            return {}
        args = obj.get("arguments") if isinstance(obj, dict) else None
        return args if isinstance(args, dict) else {}


class LedgerWriter:
    """Append-only JSONL writer. Safe across threads; each line is flushed at once."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, record: LedgerRecord) -> None:
        line = record.model_dump_json() + "\n"
        with self._lock, open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()


def read_ledger(path: str | os.PathLike[str]) -> list[LedgerRecord]:
    """Read every complete record. A half-written last line is ignored."""
    p = Path(path)
    if not p.exists():
        return []
    out: list[LedgerRecord] = []
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(LedgerRecord.model_validate_json(line))
            except ValueError:
                continue
    return out


def merge_ledgers(paths: Iterable[str | os.PathLike[str]]) -> list[LedgerRecord]:
    """Merge per-server ledgers into one timeline and renumber ``seq`` from 0."""
    records: list[LedgerRecord] = []
    for p in paths:
        records.extend(read_ledger(p))
    records.sort(key=lambda r: (r.entry.ts, r.entry.server, r.entry.seq))
    for i, r in enumerate(records):
        r.entry.seq = i
    return records
