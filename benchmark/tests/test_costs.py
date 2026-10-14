"""The price table and the cost calculator (tokens and ₹). Prices here are made-up test values."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from factories import results, run

from bench import BENCH_ROOT, costs
from bench.costs import Price, PriceError, cost_of, inr, load_prices, parse_prices


def test_shipped_price_table_is_empty() -> None:
    # Never guess prices: the file ships with no values filled in.
    assert load_prices(BENCH_ROOT / "prices.toml") == {}


def test_blank_values_mean_not_priced() -> None:
    prices = parse_prices(
        {
            "models": {
                "openai:a": {"input_inr_per_million": "", "output_inr_per_million": ""},
                "openai:b": {"input_inr_per_million": "10", "output_inr_per_million": ""},
                "openai:c": {
                    "input_inr_per_million": "12.50",
                    "output_inr_per_million": "₹1,000",
                    "source": "test",
                    "priced_on": "2026-10-14",
                },
            }
        }
    )
    assert list(prices) == ["openai:c"]
    p = prices["openai:c"]
    assert p.input_per_million == Decimal("12.50") and p.output_per_million == Decimal("1000")
    assert p.source == "test" and p.priced_on == "2026-10-14"


@pytest.mark.parametrize("bad", ["abc", "-1", "NaN", "inf"])
def test_bad_prices_are_rejected(bad: str) -> None:
    with pytest.raises(PriceError):
        parse_prices(
            {"models": {"openai:x": {"input_inr_per_million": bad, "output_inr_per_million": "1"}}}
        )


def test_bad_tables_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(PriceError):
        parse_prices({"models": {"openai:x": "1"}})
    with pytest.raises(PriceError):
        parse_prices({"models": ["x"]})
    broken = tmp_path / "p.toml"
    broken.write_text("[models\n", encoding="utf-8")
    with pytest.raises(PriceError):
        load_prices(broken)
    assert load_prices(tmp_path / "missing.toml") == {}


def test_cost_math_is_exact() -> None:
    p = Price("openai:x", Decimal("100"), Decimal("400"))
    # 1,500 in * 100/1M + 300 out * 400/1M = 0.15 + 0.12 = 0.27
    assert p.cost(1_500, 300) == Decimal("0.27")
    assert inr(Decimal("1234.5")) == "₹1,234.50"
    assert inr(Decimal("0.005")) == "₹0.01"
    assert inr(None) == "not priced"


def test_cost_of_a_results_file() -> None:
    rf = results(
        [
            run("run_cccccccc1", tokens=(1000, 100)),
            run("run_cccccccc2", tokens=(3000, 300)),
            run("run_cccccccc3", tokens=None),
        ],
        model="openai:x",
    )
    priced = cost_of(rf, {"openai:x": Price("openai:x", Decimal("100"), Decimal("400"))})
    assert (priced.runs, priced.runs_with_tokens, priced.tokens_in, priced.tokens_out) == (
        3,
        2,
        4000,
        400,
    )
    assert priced.tokens_per_run == 2200
    assert priced.total_inr == Decimal("0.56")  # 0.4 + 0.16
    assert priced.inr_per_run == Decimal("0.28")
    assert any("reported no tokens" in n for n in priced.notes)
    unpriced = cost_of(rf, {})
    assert unpriced.total_inr is None and unpriced.inr_per_run is None
    assert any("not priced" in n for n in unpriced.notes)


def test_scripted_runs_cost_nothing() -> None:
    rf = results(
        [run("run_dddddddd1", tokens=(0, 0))], framework="scripted-naive", model="scripted"
    )
    row = cost_of(rf, {})
    assert row.total_inr == Decimal(0) and not row.notes


def test_table_and_json() -> None:
    p = {"openai:x": Price("openai:x", Decimal("100"), Decimal("400"), source="test")}
    rows = [
        cost_of(results([run("run_eeeeeeee1", tokens=(1000, 100))], model="openai:x"), p, "a"),
        cost_of(results([run("run_eeeeeeee2", tokens=(1000, 100))], model="openai:y"), p, "b"),
    ]
    text = costs.table(rows)
    assert "₹0.14" in text and "not priced" in text and "note: b:" in text
    data = costs.as_json(rows)
    assert data[0]["inr_total"] == "0.14" and data[1]["inr_total"] is None
    assert data[0]["price_source"] == "test"
