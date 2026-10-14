"""Fix recipes R1-R7 as composable, framework-neutral tool-call wrappers and prompt lines.

.. code-block:: python

    from recipes import build_stack
    stack = build_stack(["R1", "R2"], seed=run_seed)
    stack.prompt_lines        # lines to append to the shared system prompt
    stack.on_tools(server, tool_dicts)
    await stack.call(server, tool, args, next_fn)

Each recipe's config label is its id (``R2``); labels combine with ``+``
(``R1+R2``), and ``all-recipes`` turns on all seven. See :mod:`recipes.fixes`.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .core import CallContext, Recipe, RecipeStack, RunState, ToolCallResult
from .fixes import NOTE, ORDER, RECIPES

__all__ = [
    "NOTE",
    "ORDER",
    "RECIPES",
    "CallContext",
    "Recipe",
    "RecipeStack",
    "RunState",
    "ToolCallResult",
    "build_stack",
]


def build_stack(
    labels: Iterable[str], *, seed: str | int = 0, options: dict[str, dict[str, Any]] | None = None
) -> RecipeStack:
    """A stack with one instance of each named recipe (``options`` = per-recipe kwargs)."""
    opts = options or {}
    recipes: list[Recipe] = []
    for label in dict.fromkeys(labels):
        cls = RECIPES.get(label)
        if cls is None:
            raise ValueError(f"unknown recipe {label!r}; known: {', '.join(RECIPES)}")
        recipes.append(cls(**opts.get(label, {})))
    return RecipeStack(recipes, seed=seed)
