"""Turn a results file into things people read: HTML, JUnit XML, a PR comment, a badge.

This package is import-light. The renderers live in submodules and are loaded
on first use, so ``import polygraph.report`` needs nothing beyond pydantic.

    from polygraph.report import render_html, write_html
    write_html(results, "polygraph-report.html")
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from ..results import ResultsFile
    from .badge import badge_text, render_badge
    from .html import render_html
    from .junit import render_junit
    from .markdown import render_pr_comment

__all__ = [
    "badge_text",
    "render_badge",
    "render_html",
    "render_junit",
    "render_pr_comment",
    "write_badge",
    "write_html",
    "write_junit",
]

_LAZY: dict[str, str] = {
    "render_html": "html",
    "render_junit": "junit",
    "render_pr_comment": "markdown",
    "render_badge": "badge",
    "badge_text": "badge",
}


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module 'polygraph.report' has no attribute {name!r}")
    return getattr(importlib.import_module(f"{__name__}.{module}"), name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))


def _write(path: str | Path, text: str) -> Path:
    """Write ``text`` as UTF-8 with ``\\n`` line endings, creating parent folders."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    return target


def write_html(results: ResultsFile, path: str | Path, *, title: str = "Polygraph report") -> Path:
    """Render the HTML report and write it to ``path``. Returns the path written."""
    from .html import render_html

    return _write(path, render_html(results, title=title))


def write_junit(results: ResultsFile, path: str | Path) -> Path:
    """Render JUnit XML and write it to ``path``. Returns the path written."""
    from .junit import render_junit

    return _write(path, render_junit(results))


def write_badge(results: ResultsFile, path: str | Path) -> Path:
    """Render the SVG badge and write it to ``path``. Returns the path written."""
    from .badge import render_badge

    return _write(path, render_badge(results))
