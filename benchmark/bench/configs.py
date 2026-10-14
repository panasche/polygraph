"""Config labels: what changes between the columns of the benchmark.

==================  =============================================================
Label               Meaning
==================  =============================================================
``default``         the framework exactly as shipped (out-of-the-box behaviour)
``best-practice``   settings the framework's maintainers suggest (see each adapter's
                    ``BEST_PRACTICE``; empty until they send some)
``R1`` .. ``R7``    one fix recipe on top of the defaults
``R1+R2`` ...       several recipes together (order does not matter)
``all-recipes``     all seven recipes
==================  =============================================================
"""

from __future__ import annotations

from dataclasses import dataclass

from recipes import RECIPES

DEFAULT = "default"
BEST_PRACTICE = "best-practice"
ALL_RECIPES = "all-recipes"


@dataclass(frozen=True)
class RunConfig:
    label: str
    recipes: tuple[str, ...] = ()
    best_practice: bool = False

    @property
    def is_default(self) -> bool:
        return not self.recipes and not self.best_practice


def resolve(label: str) -> RunConfig:
    """Parse a config label. Raises ``ValueError`` for anything unknown."""
    raw = (label or DEFAULT).strip()
    if raw == DEFAULT:
        return RunConfig(DEFAULT)
    if raw == BEST_PRACTICE:
        return RunConfig(BEST_PRACTICE, best_practice=True)
    if raw == ALL_RECIPES:
        return RunConfig(ALL_RECIPES, recipes=tuple(RECIPES))
    parts = [p.strip().upper() for p in raw.split("+")]
    unknown = [p for p in parts if p not in RECIPES]
    if unknown:
        raise ValueError(
            f"unknown config label {label!r}: use {DEFAULT!r}, {BEST_PRACTICE!r}, "
            f"{ALL_RECIPES!r}, a recipe id ({', '.join(RECIPES)}) or ids joined with '+'"
        )
    ordered = tuple(r for r in RECIPES if r in parts)
    return RunConfig("+".join(ordered), recipes=ordered)
