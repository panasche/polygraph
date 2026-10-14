"""Tests for polygraph.privacy (masking and stripping before upload)."""

from __future__ import annotations

import sys
import types
from datetime import UTC, datetime
from typing import Any

import pytest

from polygraph.metrics import summarize
from polygraph.privacy import (
    MaskError,
    apply_mask,
    load_callable,
    prepare_for_upload,
    strip_free_text,
)
from polygraph.results import (
    ClaimCheck,
    Coverage,
    Environment,
    FaultName,
    FaultSpec,
    Happened,
    JudgeVerdict,
    LedgerEntry,
    Outcome,
    Payload,
    ResultsFile,
    Run,
    SecondOpinion,
    StateCheck,
    iter_payloads,
    parse,
)

TS = datetime(2026, 10, 13, 9, 30, tzinfo=UTC)


def make_results(*, with_text: bool = True) -> ResultsFile:
    """A small valid results file with invented data and (optionally) payload text."""

    def p(text: str) -> Payload:
        return Payload.of(text, include_text=with_text)

    entry = LedgerEntry(
        seq=0,
        ts=TS,
        server="bank",
        method="tools/call",
        tool="pay_invoice",
        fault=FaultName.LOST_RESPONSE,
        asked=p('{"invoice": "INV-42", "payer": "Customer A"}'),
        happened=Happened(
            forwarded=1,
            server_result=p("paid INV-42 for customer-a@example.test"),
            note="result withheld from agent (lost_response)",
        ),
        told=None,
    )
    run1 = Run(
        id="run_0001aaaa",
        test_id="tests/test_bank.py::test_pay",
        task="Pay invoice INV-42",
        fault=FaultSpec(name=FaultName.LOST_RESPONSE, server="bank", tool="pay_invoice"),
        seed=1,
        started_at=TS,
        duration_ms=12.5,
        outcome=Outcome.DOUBLE_ACTION,
        reasons=["pay_invoice forwarded twice"],
        judge=JudgeVerdict(claimed_success=True, rationale="Agent said INV-42 is paid"),
        second_opinion=SecondOpinion(
            tool="agent-polygraph",
            flagged_lie=True,
            agrees_with_polygraph=True,
            detail="Answer mentions Customer A",
        ),
        final_answer=p("Paid INV-42 for Customer A."),
        state_checks=[StateCheck(name="paid_once", passed=False, detail="2 payments for INV-42")],
        ledger=[entry],
        error="ValueError: invoice INV-42 for customer-a@example.test",
    )
    run2 = Run(
        id="run_0002bbbb",
        test_id="tests/test_bank.py::test_pay",
        seed=1,
        started_at=TS,
        duration_ms=10.0,
        outcome=Outcome.RECOVERED,
        error="something odd happened",
    )
    runs = [run1, run2]
    coverage = Coverage(applicable=1, covered=1)
    return ResultsFile(
        generated_at=TS,
        content_included=with_text,
        environment=Environment(
            polygraph_version="0.1.0", python_version="3.11.9", platform="linux", seed=1
        ),
        coverage=coverage,
        runs=runs,
        server_claims=[
            ClaimCheck(
                server="bank",
                tool="pay_invoice",
                claim="idempotent",
                held=False,
                evidence="second call created payment 2",
            )
        ],
        summary=summarize(runs, coverage),
    )


def upper(text: str) -> str:
    return text.upper()


# --------------------------------------------------------------------------- #
# load_callable
# --------------------------------------------------------------------------- #


def test_load_callable_module_and_dotted_attr(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = types.ModuleType("pg_fake_masks")

    class Masks:
        @staticmethod
        def hide(text: str) -> str:
            return "x" * len(text)

    mod.Masks = Masks  # type: ignore[attr-defined]
    mod.plain = upper  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pg_fake_masks", mod)
    assert load_callable("pg_fake_masks:plain")("ab") == "AB"
    assert load_callable("pg_fake_masks:Masks.hide")("abc") == "xxx"
    assert load_callable("os.path:join") is __import__("os").path.join


@pytest.mark.parametrize("spec", ["nocolon", "a:b:c", ":attr", "mod:", "mod:1bad", "bad mod:x"])
def test_load_callable_bad_spec(spec: str) -> None:
    with pytest.raises(ValueError, match="module.path:attribute"):
        load_callable(spec)


def test_load_callable_missing_module_and_attr() -> None:
    with pytest.raises(ImportError, match="pg_does_not_exist"):
        load_callable("pg_does_not_exist.sub:fn")
    with pytest.raises(AttributeError, match="nope"):
        load_callable("os.path:nope")
    with pytest.raises(TypeError, match="not callable"):
        load_callable("os:sep")


# --------------------------------------------------------------------------- #
# apply_mask
# --------------------------------------------------------------------------- #


def test_apply_mask_covers_every_text_and_keeps_hashes() -> None:
    original = make_results()
    before = original.to_json()
    masked = apply_mask(original, lambda t: "[MASKED]")

    assert original.to_json() == before  # input untouched
    texts = [p.text for p in iter_payloads(masked)]
    assert texts and all(t == "[MASKED]" for t in texts)
    for a, b in zip(iter_payloads(original), iter_payloads(masked), strict=True):
        assert (a.sha256, a.size) == (b.sha256, b.size)

    r = masked.runs[0]
    assert r.task == "[MASKED]"
    assert r.error == "[MASKED]"
    assert r.reasons == ["[MASKED]"]
    assert r.state_checks[0].detail == "[MASKED]"
    assert r.judge is not None and r.judge.rationale == "[MASKED]"
    assert r.second_opinion is not None and r.second_opinion.detail == "[MASKED]"
    assert r.ledger[0].happened.note == "[MASKED]"
    assert masked.server_claims[0].evidence == "[MASKED]"
    assert masked.runs[1].task is None  # None stays None
    assert masked.content_included is True
    dumped = masked.to_json()
    assert "INV-42" not in dumped and "Customer A" not in dumped


def test_apply_mask_raising_fails_closed_without_leaking_text() -> None:
    original = make_results()
    before = original.to_json()

    def boom(text: str) -> str:
        if "Customer A" in text:
            raise RuntimeError(text)
        return text

    with pytest.raises(MaskError) as info:
        apply_mask(original, boom)
    assert "Customer A" not in str(info.value)
    assert "INV-42" not in str(info.value)
    assert isinstance(info.value.__cause__, RuntimeError)
    assert original.to_json() == before


def test_apply_mask_non_str_fails_closed() -> None:
    original = make_results()
    with pytest.raises(MaskError, match="returned NoneType"):
        apply_mask(original, lambda t: None)  # type: ignore[arg-type, return-value]
    with pytest.raises(MaskError, match="returned bytes"):
        apply_mask(original, lambda t: t.encode())  # type: ignore[arg-type, return-value]


# --------------------------------------------------------------------------- #
# strip_free_text
# --------------------------------------------------------------------------- #


def test_strip_free_text() -> None:
    original = make_results()
    before = original.to_json()
    s = strip_free_text(original)
    assert original.to_json() == before

    assert s.content_included is False
    assert all(p.text is None for p in iter_payloads(s))
    r = s.runs[0]
    assert r.state_checks[0].detail is None
    assert r.judge is not None and r.judge.rationale is None
    assert r.second_opinion is not None and r.second_opinion.detail is None
    assert r.error == "ValueError"
    assert s.runs[1].error == "Exception"  # no type-name prefix → generic
    # Kept: Polygraph writes these without payload text.
    assert r.reasons == ["pay_invoice forwarded twice"]
    assert r.ledger[0].happened.note == "result withheld from agent (lost_response)"
    assert s.server_claims[0].evidence == "second call created payment 2"
    assert r.task == "Pay invoice INV-42"
    assert "customer-a@example.test" not in s.to_json()


def test_strip_free_text_keeps_dotted_type_name() -> None:
    rf = make_results(with_text=False)
    rf.runs[0].error = "httpx.ReadTimeout: timed out"
    assert strip_free_text(rf).runs[0].error == "httpx.ReadTimeout"


# --------------------------------------------------------------------------- #
# prepare_for_upload
# --------------------------------------------------------------------------- #


def test_prepare_default_strips_even_when_text_present() -> None:
    out = prepare_for_upload(make_results())
    assert out.content_included is False
    assert all(p.text is None for p in iter_payloads(out))
    assert parse(out.to_json()) == out


def test_prepare_include_content_requires_mask() -> None:
    with pytest.raises(MaskError, match="--include-content needs a mask"):
        prepare_for_upload(make_results(), include_content=True)


def test_prepare_include_content_with_callable_and_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    out = prepare_for_upload(make_results(), include_content=True, mask=upper)
    assert out.content_included is True
    assert out.runs[0].final_answer is not None
    assert out.runs[0].final_answer.text == "PAID INV-42 FOR CUSTOMER A."

    mod = types.ModuleType("pg_fake_mask2")
    mod.mask = lambda t: "***"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pg_fake_mask2", mod)
    out2 = prepare_for_upload(make_results(), include_content=True, mask="pg_fake_mask2:mask")
    assert {p.text for p in iter_payloads(out2)} == {"***"}


def test_prepare_include_content_without_text_clears_flag() -> None:
    out = prepare_for_upload(make_results(with_text=False), include_content=True, mask=upper)
    assert out.content_included is False


@pytest.mark.parametrize("spec", ["not a spec", "pg_missing_mod:x", "os.path:nope", "os:sep"])
def test_prepare_bad_mask_spec_is_mask_error(spec: str) -> None:
    with pytest.raises(MaskError, match="could not load mask"):
        prepare_for_upload(make_results(), include_content=True, mask=spec)


def test_prepare_non_callable_mask() -> None:
    bad: Any = 42
    with pytest.raises(MaskError, match="not callable"):
        prepare_for_upload(make_results(), include_content=True, mask=bad)


def test_prepare_validation_failure_is_mask_error_without_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import polygraph.privacy as privacy

    def bad_parse(data: Any) -> ResultsFile:
        raise ValueError("input_value='Customer A'")

    monkeypatch.setattr(privacy, "parse", bad_parse)
    with pytest.raises(MaskError) as info:
        prepare_for_upload(make_results())
    assert "Customer A" not in str(info.value)
