"""Small helpers for writing benchmark tasks (used by ``tasks/test_*.py``)."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, TypeVar

import pytest

from bench.tasks import HEADLINE, TASKS

F = TypeVar("F", bound=Callable[..., Any])


def task(task_id: str) -> Callable[[F], F]:
    """Mark a test as benchmark task ``task_id``: its label, its world and (if pinned) its fault."""
    t = TASKS[task_id]

    def deco(fn: F) -> F:
        marks = [pytest.mark.polygraph_task(t.instruction), pytest.mark.polygraph_world(t.world)]
        pin = HEADLINE.get(task_id)
        if pin is not None:
            marks.append(pytest.mark.polygraph(fault=pin.fault, tool=pin.tool, **dict(pin.params)))
            marks.append(pytest.mark.headline)
            if pin.model_check:
                marks.append(pytest.mark.model_check)
        for m in marks:
            fn = m(fn)
        fn.bench_task_id = task_id  # type: ignore[attr-defined]
        return fn

    return deco


def mentions(answer: str | None, *patterns: str) -> bool:
    """True if the answer matches any of the (case-insensitive) regex patterns."""
    text = answer or ""
    return any(re.search(p, text, re.I) for p in patterns)
