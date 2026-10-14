"""Cost per run, in tokens and in ₹, from results files and ``prices.toml``.

Prices are never guessed. A model without a price (or with an empty one) is
reported as "not priced", and its ₹ figures stay empty. Money uses ``Decimal``
so the totals add up exactly.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from polygraph.results import ResultsFile

MILLION = Decimal(1_000_000)
PAISE = Decimal("0.01")


class PriceError(ValueError):
    pass


@dataclass(frozen=True)
class Price:
    model: str
    input_per_million: Decimal
    output_per_million: Decimal
    source: str | None = None
    priced_on: str | None = None

    def cost(self, tokens_in: int, tokens_out: int) -> Decimal:
        return (
            Decimal(tokens_in) * self.input_per_million
            + Decimal(tokens_out) * self.output_per_million
        ) / MILLION


def _money(raw: Any, where: str) -> Decimal | None:
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "").replace("₹", "")
    if not text:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise PriceError(f"{where}: {raw!r} is not a number") from exc
    if not value.is_finite() or value < 0:
        raise PriceError(f"{where}: price must be a positive number")
    return value


def parse_prices(data: Mapping[str, Any]) -> dict[str, Price]:
    """``{"models": {"provider:model": {...}}}`` -> prices that are fully filled in."""
    out: dict[str, Price] = {}
    models = data.get("models")
    if models is None:
        models = {}
    if not isinstance(models, Mapping):
        raise PriceError("[models] must be a table")
    for model, entry in models.items():
        if not isinstance(entry, Mapping):
            raise PriceError(f'[models."{model}"] must be a table')
        pin = _money(entry.get("input_inr_per_million"), f"{model} input_inr_per_million")
        pout = _money(entry.get("output_inr_per_million"), f"{model} output_inr_per_million")
        if pin is None or pout is None:
            continue  # left blank: not priced
        out[str(model)] = Price(
            model=str(model),
            input_per_million=pin,
            output_per_million=pout,
            source=str(entry.get("source") or "") or None,
            priced_on=str(entry.get("priced_on") or "") or None,
        )
    return out


def load_prices(path: Path) -> dict[str, Price]:
    if not path.exists():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise PriceError(f"{path}: {exc}") from exc
    return parse_prices(data)


@dataclass
class CostRow:
    """Cost of one results file: one (framework, config, model) cell."""

    label: str
    model: str | None
    runs: int = 0
    runs_with_tokens: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    price: Price | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def tokens(self) -> int:
        return self.tokens_in + self.tokens_out

    @property
    def tokens_per_run(self) -> float | None:
        return self.tokens / self.runs_with_tokens if self.runs_with_tokens else None

    @property
    def total_inr(self) -> Decimal | None:
        if self.price is None:
            return None
        return self.price.cost(self.tokens_in, self.tokens_out)

    @property
    def inr_per_run(self) -> Decimal | None:
        total = self.total_inr
        if total is None or not self.runs_with_tokens:
            return None
        return total / Decimal(self.runs_with_tokens)


def cost_of(results: ResultsFile, prices: Mapping[str, Price], label: str = "") -> CostRow:
    env = results.environment
    row = CostRow(label=label or _label(results), model=env.model)
    for run in results.runs:
        row.runs += 1
        if run.tokens is not None:
            row.runs_with_tokens += 1
            row.tokens_in += run.tokens.input
            row.tokens_out += run.tokens.output
    if env.model == "scripted":  # the dry run calls no model: it really costs nothing
        row.price = Price("scripted", Decimal(0), Decimal(0), source="no model used")
    elif env.model and env.model in prices:
        row.price = prices[env.model]
    elif env.model:
        row.notes.append(f"{env.model} is not priced in prices.toml")
    if row.runs and row.runs_with_tokens < row.runs:
        row.notes.append(f"{row.runs - row.runs_with_tokens} runs reported no tokens")
    return row


def _label(results: ResultsFile) -> str:
    e = results.environment
    return " · ".join(x for x in (e.framework, e.config_label, e.model) if x)


def inr(value: Decimal | None) -> str:
    if value is None:
        return "not priced"
    return "₹" + format(value.quantize(PAISE, rounding=ROUND_HALF_UP), ",f")


def table(rows: Iterable[CostRow]) -> str:
    """A plain-text cost table, one line per results file, plus a total."""
    rows = list(rows)
    head = (
        f"{'cell':<48} {'runs':>5} {'tokens in':>11} {'tokens out':>11} {'tok/run':>9} "
        f"{'₹/run':>12} {'₹ total':>12}"
    )
    lines = [head, "-" * len(head)]
    for r in rows:
        per = f"{r.tokens_per_run:,.0f}" if r.tokens_per_run is not None else "-"
        lines.append(
            f"{r.label[:48]:<48} {r.runs:>5} {r.tokens_in:>11,} {r.tokens_out:>11,} {per:>9} "
            f"{inr(r.inr_per_run):>12} {inr(r.total_inr):>12}"
        )
    priced = [r.total_inr for r in rows if r.total_inr is not None]
    all_priced = len(priced) == len(rows) and rows
    total = sum(priced, Decimal(0)) if all_priced else None
    lines.append("-" * len(head))
    lines.append(
        f"{'total':<48} {sum(r.runs for r in rows):>5} {sum(r.tokens_in for r in rows):>11,} "
        f"{sum(r.tokens_out for r in rows):>11,} {'':>9} {'':>12} {inr(total):>12}"
    )
    for r in rows:
        for note in r.notes:
            lines.append(f"note: {r.label}: {note}")
    return "\n".join(lines)


def as_json(rows: Iterable[CostRow]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        out.append(
            {
                "cell": r.label,
                "model": r.model,
                "runs": r.runs,
                "runs_with_tokens": r.runs_with_tokens,
                "tokens_in": r.tokens_in,
                "tokens_out": r.tokens_out,
                "tokens_per_run": r.tokens_per_run,
                "inr_per_run": None if r.inr_per_run is None else str(r.inr_per_run),
                "inr_total": None if r.total_inr is None else str(r.total_inr),
                "price_source": r.price.source if r.price else None,
                "priced_on": r.price.priced_on if r.price else None,
                "notes": r.notes,
            }
        )
    return out
