"""The local JSONL ledger: write, read back, merge."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from polygraph.ledger import LedgerRecord, LedgerWriter, merge_ledgers, read_ledger
from polygraph.results import Happened, LedgerEntry, Payload

T0 = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)


def rec(seq: int, *, server: str = "bank", ts: datetime = T0, asked=None, **kw) -> LedgerRecord:
    asked = (
        {"name": "get_invoice", "arguments": {"invoice_id": "INV-42"}} if asked is None else asked
    )
    return LedgerRecord(
        entry=LedgerEntry(
            seq=seq,
            ts=ts,
            server=server,
            method="tools/call",
            tool="get_invoice",
            asked=Payload.of(asked),
            happened=Happened(forwarded=1),
        ),
        **kw,
    )


def test_round_trip(tmp_path):
    path = tmp_path / "nested" / "dir" / "ledger.jsonl"
    w = LedgerWriter(path)
    assert path.parent.is_dir()
    a = rec(0, canaries=["PG-CANARY-00000000"], event="rug_pull", extra={"k": 1})
    b = rec(1, trace_id="a" * 32, span_id="b" * 16, duplicate_is_error=True)
    w.write(a)
    w.write(b)
    out = read_ledger(path)
    assert [r.model_dump() for r in out] == [a.model_dump(), b.model_dump()]
    assert path.read_text(encoding="utf-8").count("\n") == 2


def test_missing_file(tmp_path):
    assert read_ledger(tmp_path / "nope.jsonl") == []


def test_half_written_last_line_and_blank_lines(tmp_path):
    path = tmp_path / "ledger.jsonl"
    LedgerWriter(path).write(rec(0))
    good = path.read_text(encoding="utf-8")
    second = rec(1).model_dump_json()
    path.write_text(
        "\n\n" + good + "   \n" + '{"not": "a record"}\n' + second[: len(second) // 2],
        encoding="utf-8",
    )
    out = read_ledger(path)
    assert [r.entry.seq for r in out] == [0]


def test_unknown_fields_ignored(tmp_path):
    path = tmp_path / "ledger.jsonl"
    data = rec(0).model_dump(mode="json")
    data["future_field"] = 1
    import json

    path.write_text(json.dumps(data) + "\n", encoding="utf-8")
    assert len(read_ledger(path)) == 1


def test_merge_orders_and_renumbers(tmp_path):
    bank = tmp_path / "ledger-bank.jsonl"
    shop = tmp_path / "ledger-shop.jsonl"
    wb, ws = LedgerWriter(bank), LedgerWriter(shop)
    wb.write(rec(0, server="bank", ts=T0))
    wb.write(rec(1, server="bank", ts=T0 + timedelta(seconds=2)))
    ws.write(rec(0, server="shop", ts=T0 + timedelta(seconds=1)))
    ws.write(rec(1, server="shop", ts=T0 + timedelta(seconds=2)))
    # same ts within one server: original seq breaks the tie
    ws.write(rec(5, server="shop", ts=T0 + timedelta(seconds=3)))
    ws.write(rec(4, server="shop", ts=T0 + timedelta(seconds=3)))
    out = merge_ledgers([shop, bank, tmp_path / "missing.jsonl"])
    order = [(r.entry.server, r.entry.ts - T0) for r in out]
    assert order == [
        ("bank", timedelta(0)),
        ("shop", timedelta(seconds=1)),
        ("bank", timedelta(seconds=2)),  # same ts: server name breaks the tie
        ("shop", timedelta(seconds=2)),
        ("shop", timedelta(seconds=3)),
        ("shop", timedelta(seconds=3)),
    ]
    assert [r.entry.seq for r in out] == list(range(6))
    assert merge_ledgers([]) == []


def test_merge_tie_break_by_seq(tmp_path):
    p = tmp_path / "l.jsonl"
    w = LedgerWriter(p)
    w.write(rec(9, asked={"name": "b"}))
    w.write(rec(3, asked={"name": "a"}))
    out = merge_ledgers([p])
    assert [r.entry.asked.text for r in out] == ['{"name":"a"}', '{"name":"b"}']


def test_arguments():
    assert rec(0).arguments == {"invoice_id": "INV-42"}
    assert rec(0, asked={"name": "x"}).arguments == {}
    assert rec(0, asked={"name": "x", "arguments": [1]}).arguments == {}
    assert rec(0, asked=[1, 2]).arguments == {}
    bad = rec(0)
    bad.entry.asked = Payload.of("{not json")
    assert bad.arguments == {}
    no_text = rec(0)
    no_text.entry.asked = no_text.entry.asked.without_text()
    assert no_text.arguments == {}
