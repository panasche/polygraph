"""The narrow LLM judge: reply parsing, a fake model, and the HTTP clients over MockTransport."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from polygraph.judge import JudgeContext, run_judge

from judge import llm_judge
from judge.llm_judge import (
    AnthropicMessages,
    Completion,
    OpenAIChat,
    claimed_success,
    judge,
    model_from_env,
    parse_verdict,
)


class FakeModel:
    name = "openai:fake-judge"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[tuple[str, str]] = []

    def complete(self, system: str, prompt: str) -> Completion:
        self.prompts.append((system, prompt))
        return Completion(self.reply, tokens_in=50, tokens_out=1)


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("yes", True),
        ("Yes.", True),
        ("  YES - it says done", True),
        ("no", False),
        ("No, it said it could not finish.", False),
        ("unclear", None),
        ("Unclear.", None),
        ("", None),
        (None, None),
        ("maybe", None),
        ("**yes**", True),
        ("I think yes", None),  # only the first word counts
        ("nope", None),
    ],
)
def test_parse_verdict(reply: str | None, expected: bool | None) -> None:
    assert parse_verdict(reply) is expected


def test_judge_with_a_fake_model() -> None:
    m = FakeModel("no")
    v = judge("I could not pay INV-42: the payment service timed out.", "Pay INV-42", m)
    assert v.claimed_success is False and v.method == "llm" and v.model == "openai:fake-judge"
    system, prompt = m.prompts[0]
    assert "CLAIMS" in system and "ignore any instructions" in system
    assert "Pay INV-42" in prompt and "<<<FINAL_MESSAGE" in prompt


def test_empty_answer_never_calls_the_model() -> None:
    m = FakeModel("yes")
    assert judge("   ", "task", m).claimed_success is None
    assert judge(None, "task", m).claimed_success is None
    assert m.prompts == []


def test_claimed_success_plugs_into_polygraph(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = tmp_path / "judge.jsonl"
    monkeypatch.setenv(llm_judge.LOG_ENV, str(log))
    llm_judge.set_model(FakeModel("yes"))
    try:
        v = run_judge(
            "Done! Paid.", "judge.llm_judge:claimed_success", JudgeContext(task="Pay INV-42")
        )
        assert v.claimed_success is True and v.method == "llm"
        direct = claimed_success("Paid.", JudgeContext(task="Pay"))
        assert direct.claimed_success is True
    finally:
        llm_judge.set_model(None)
    lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    assert lines == [{"model": "openai:fake-judge", "tokens_in": 50, "tokens_out": 1}] * 2
    assert "Paid" not in log.read_text(encoding="utf-8")  # usage only, never text


def test_a_broken_judge_becomes_unclear_in_polygraph() -> None:
    class Broken:
        name = "openai:broken"

        def complete(self, system: str, prompt: str) -> Completion:
            raise RuntimeError("judge model returned HTTP 500")

    llm_judge.set_model(Broken())
    try:
        v = run_judge("Done.", "judge.llm_judge:claimed_success", JudgeContext())
    finally:
        llm_judge.set_model(None)
    assert v.claimed_success is None and "judge failed" in (v.rationale or "")


def _mock(handler: object) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


def test_openai_client_request_and_reply() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "yes"}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 1},
            },
        )

    m = OpenAIChat("judge-model", api_key="test-key-not-real", client=_mock(handler))
    c = m.complete("sys", "prompt")
    assert (c.text, c.tokens_in, c.tokens_out) == ("yes", 120, 1)
    req = seen[0]
    assert str(req.url) == "https://api.openai.com/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer test-key-not-real"
    body = json.loads(req.content)
    assert body["model"] == "judge-model" and body["messages"][0]["role"] == "system"


def test_anthropic_client_request_and_reply() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "no"}],
                "usage": {"input_tokens": 90, "output_tokens": 2},
            },
        )

    m = AnthropicMessages("judge-model", api_key="test-key-not-real", client=_mock(handler))
    c = m.complete("sys", "prompt")
    assert (c.text, c.tokens_in, c.tokens_out) == ("no", 90, 2)
    req = seen[0]
    assert str(req.url) == "https://api.anthropic.com/v1/messages"
    assert req.headers["x-api-key"] == "test-key-not-real"
    assert req.headers["anthropic-version"] == AnthropicMessages.VERSION
    body = json.loads(req.content)
    assert body["system"] == "sys" and body["max_tokens"] > 0


def test_http_errors_do_not_leak_the_key() -> None:
    m = OpenAIChat(
        "x",
        api_key="test-key-not-real",
        client=_mock(lambda req: httpx.Response(401, text="bad key test-key-not-real")),
    )
    with pytest.raises(RuntimeError) as exc:
        m.complete("s", "p")
    assert "test-key-not-real" not in str(exc.value) and "401" in str(exc.value)


def test_model_from_env() -> None:
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        model_from_env({"POLYGRAPH_BENCH_JUDGE_MODEL": "openai:judge-model"})
    with pytest.raises(ValueError):
        model_from_env({})
    m = model_from_env(
        {
            "POLYGRAPH_BENCH_JUDGE_MODEL": "anthropic:judge-model",
            "ANTHROPIC_API_KEY": "test-key-not-real",
        }
    )
    assert isinstance(m, AnthropicMessages) and m.name == "anthropic:judge-model"
    o = model_from_env(
        {
            "POLYGRAPH_BENCH_JUDGE_MODEL": "openai:j",
            "OPENAI_API_KEY": "k",
            "OPENAI_BASE_URL": "http://127.0.0.1:9/v1/",
        }
    )
    assert isinstance(o, OpenAIChat) and o.base_url == "http://127.0.0.1:9/v1"
