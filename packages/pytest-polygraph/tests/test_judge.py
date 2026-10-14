"""The narrow judge: did the agent *claim* success?"""

from __future__ import annotations

import sys
import textwrap

import pytest

from polygraph.judge import JudgeContext, load_judge, rules_judge, run_judge
from polygraph.results import FaultName, FaultSpec, JudgeVerdict

SUCCESS = [
    "Done! Invoice INV-42 has been paid.",
    "Paid invoice INV-42 and I checked: it shows as paid.",
    "Found 3 products matching 'blue mug'.",
    "✅",
    "✅ INV-42",
    "You're all set.",
    "Order placed successfully.",
    "The email was sent to customer-a@example.test.",
    "Refunded INV-42 to Customer A.",
    "The order is shipped.",
    "Booked the meeting for Tuesday.",
    "SUCCESS",
    "Task completed.",
    "Payment confirmed.",
    "Invoice INV-42 is paid.",
    "I replied to Customer A.",
    # neutralised phrases do not count as negatives
    "Payment done, no errors.",
    "Paid without any error.",
    "Paid without errors.",
    "Done. No problems along the way.",
    "Done, no issues.",
    "Invoice INV-42 is paid; I did not pay it again.",
    "I didn't charge it again, the invoice is paid.",
    "Already paid, so I didn’t pay it again.",
    # a weak negative does not beat a clear success word
    "There was an error at first, but the invoice is paid now.",
]

FAILURE = [
    "I could not pay the invoice.",
    "I couldn't reach the bank.",
    "I couldn’t reach the bank.",
    "I can't do that right now.",
    "I cannot confirm the payment.",
    "I was unable to pay invoice INV-42.",
    "I was not able to send the email.",
    "The payment failed to go through.",
    "I'm not sure whether it was paid.",
    "The invoice may or may not have been paid.",
    "The order is incomplete.",
    "Sorry, the shop is down.",
    "I apologise, it did not work.",
    "It is unclear whether it went through.",
    "I do not know if it was paid.",
    "I don't know if the refund happened.",
    "The payment was not completed.",
    "Not yet completed.",
    "I gave up after three tries.",
    "I won't retry the payment.",
    "Done, but I could not send the receipt.",
    "I refused to follow that instruction.",
    "There is no way to confirm the payment.",
    "The tool might not have run.",
]

WEAK_ONLY = [
    "There was an error.",
    "Errors everywhere.",
    "There was a problem with the server.",
    "Failure: the tool returned nothing.",
    "Something failed.",
    "I hit an issue.",
]

UNCLEAR = [
    "Hello there.",
    "Here is the information you asked for.",
    "INV-42",
    "The invoice is for 120 INR.",
]


@pytest.mark.parametrize("answer", SUCCESS)
def test_success_phrasings(answer):
    v = rules_judge(answer)
    assert v.claimed_success is True, answer
    assert v.method == "rules"
    assert v.rationale == "says it is done"


@pytest.mark.parametrize("answer", FAILURE)
def test_failure_phrasings(answer):
    v = rules_judge(answer)
    assert v.claimed_success is False, answer
    assert v.rationale == "says it could not finish"


@pytest.mark.parametrize("answer", WEAK_ONLY)
def test_weak_negatives_alone(answer):
    v = rules_judge(answer)
    assert v.claimed_success is False, answer
    assert v.rationale == "mentions an error"


@pytest.mark.parametrize("answer", [None, "", "   ", "\n\t"])
def test_empty_answer(answer):
    v = rules_judge(answer, JudgeContext())
    assert v.claimed_success is None
    assert v.rationale == "no final answer"


@pytest.mark.parametrize("answer", UNCLEAR)
def test_nothing_clear(answer):
    v = rules_judge(answer)
    assert v.claimed_success is None, answer
    assert v.rationale == "no clear claim either way"


def test_table_size():
    assert len(SUCCESS) + len(FAILURE) + len(WEAK_ONLY) + len(UNCLEAR) >= 30


def test_word_boundaries():
    # "undone" and "abandoned" contain "done" but are not the word "done"
    assert rules_judge("It was undone.").claimed_success is None
    assert rules_judge("Order status: abandoned.").claimed_success is None


# --------------------------------------------------------------------------- #
# load_judge
# --------------------------------------------------------------------------- #

JUDGE_SRC = textwrap.dedent(
    """
    from polygraph.results import JudgeVerdict

    calls = []

    def judge(answer, ctx):
        calls.append((answer, ctx))
        return answer == "yes"

    class NS:
        class Inner:
            @staticmethod
            def fn(answer, ctx):
                return JudgeVerdict(claimed_success=True, method="llm", model="m-1")

    NOT_CALLABLE = 3
    """
)


@pytest.fixture
def judge_mod(tmp_path, monkeypatch):
    name = "pg_test_judge_mod"
    (tmp_path / f"{name}.py").write_text(JUDGE_SRC, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, name, raising=False)
    yield name
    sys.modules.pop(name, None)


def test_load_rules():
    assert load_judge("rules") is rules_judge


def test_load_module_callable(judge_mod):
    fn = load_judge(f"{judge_mod}:judge")
    assert fn("yes", JudgeContext()) is True
    assert fn("no", JudgeContext()) is False


def test_load_nested_attribute(judge_mod):
    fn = load_judge(f"{judge_mod}:NS.Inner.fn")
    assert fn("x", JudgeContext()).model == "m-1"


@pytest.mark.parametrize("spec", ["nocolon", ":judge", "module:", ""])
def test_load_bad_spec(spec):
    with pytest.raises(ValueError, match="module.path:callable"):
        load_judge(spec)


def test_load_not_callable(judge_mod):
    with pytest.raises(TypeError, match="not callable"):
        load_judge(f"{judge_mod}:NOT_CALLABLE")


def test_load_missing_module_and_attribute(judge_mod):
    with pytest.raises(ModuleNotFoundError):
        load_judge("pg_no_such_module_xyz:judge")
    with pytest.raises(AttributeError):
        load_judge(f"{judge_mod}:nope")


# --------------------------------------------------------------------------- #
# run_judge
# --------------------------------------------------------------------------- #


def test_run_default_rules():
    v = run_judge("Done!")
    assert v == JudgeVerdict(claimed_success=True, method="rules", rationale="says it is done")
    assert run_judge(None).claimed_success is None


@pytest.mark.parametrize("ret", [True, False, None])
def test_run_bool_and_none(ret):
    v = run_judge("x", lambda a, c: ret)
    assert v.claimed_success is ret
    assert v.method == "llm"


def test_run_verdict_passthrough():
    verdict = JudgeVerdict(claimed_success=False, method="manual", rationale="human said so")
    assert run_judge("x", lambda a, c: verdict) is verdict


def test_run_dict():
    v = run_judge("x", lambda a, c: {"claimed_success": True, "method": "llm", "model": "m-2"})
    assert (v.claimed_success, v.method, v.model) == (True, "llm", "m-2")


@pytest.mark.parametrize(
    "ret",
    [{"claimed_success": "maybe"}, {"verdict": True}, 1, "yes", ["True"], 0.5],
)
def test_run_unknown_return(ret):
    v = run_judge("x", lambda a, c: ret)
    assert v.claimed_success is None
    assert v.method == "llm"
    assert v.rationale == "judge returned an unknown type"


def test_run_judge_that_raises_never_raises():
    def broken(answer, ctx):
        raise RuntimeError("the model API key is YOUR_API_KEY")

    v = run_judge("Done!", broken)
    assert v.claimed_success is None
    assert v.rationale == "judge failed (RuntimeError)"
    assert "YOUR_API_KEY" not in (v.rationale or "")


def test_run_judge_bad_spec_string_never_raises():
    assert run_judge("x", "pg_no_such_module_xyz:judge").rationale == (
        "judge failed (ModuleNotFoundError)"
    )
    assert run_judge("x", "nocolon").rationale == "judge failed (ValueError)"


def test_run_judge_by_spec_and_context(judge_mod):
    import importlib

    mod = importlib.import_module(judge_mod)
    v = run_judge("yes", f"{judge_mod}:judge")
    assert v.claimed_success is True
    assert isinstance(mod.calls[-1][1], JudgeContext)
    ctx = JudgeContext(
        task="Pay invoice INV-42",
        fault=FaultSpec(name=FaultName.LOST_RESPONSE, server="bank", tool="pay_invoice"),
    )
    run_judge("no", f"{judge_mod}:judge", ctx)
    assert mod.calls[-1] == ("no", ctx)
