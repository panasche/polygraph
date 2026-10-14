"""Each framework's full agent loop, offline, with that framework's own fake/function model.

The fake model asks for ``get_invoice`` once and then answers. The tool call goes
through the framework's real MCP client and the real Polygraph proxy to the real
bank world, so this checks the adapter end to end (answer, tokens, ledger) for
``default`` and for ``all-recipes``. Skips when the framework is not installed.
No API key, no network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from polygraph.ledger import read_ledger

from adapters import adapter_class
from bench.agent import AgentCrash
from bench.models import ModelSpec
from bench.probe import proxied_bank
from bench.prompt import SYSTEM_PROMPT

pytestmark = pytest.mark.e2e
ANSWER = "Invoice INV-42 is open."
CONFIGS = ["default", "all-recipes"]


def _agent(framework: str, tmp_path: Path, config_label: str) -> tuple[Any, Path]:
    cls = adapter_class(framework)
    if not cls.available():
        pytest.skip(f'{framework} not installed (pip install -e ".[{cls.extra}]")')
    servers, ledger = proxied_bank(tmp_path)
    return cls(servers, ModelSpec("openai", "fake"), SYSTEM_PROMPT, config_label), ledger


def _called_get_invoice(ledger: Path) -> bool:
    calls = [r for r in read_ledger(ledger) if r.entry.method == "tools/call"]
    return any(r.entry.tool == "get_invoice" for r in calls)


# --------------------------------------------------------------------------- langgraph
def _langgraph_fake() -> Any:
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    class ToolFake(GenericFakeChatModel):
        def bind_tools(self, tools: Any, **kw: Any) -> Any:
            return self

    return ToolFake(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "get_invoice",
                            "args": {"invoice_id": "INV-42"},
                            "id": "call_1",
                            "type": "tool_call",
                        }
                    ],
                    usage_metadata={"input_tokens": 11, "output_tokens": 3, "total_tokens": 14},
                ),
                AIMessage(
                    content=ANSWER,
                    usage_metadata={"input_tokens": 20, "output_tokens": 5, "total_tokens": 25},
                ),
            ]
        )
    )


@pytest.mark.parametrize("config_label", CONFIGS)
def test_langgraph_loop(tmp_path: Path, config_label: str) -> None:
    agent, ledger = _agent("langgraph", tmp_path, config_label)
    agent.model_override = _langgraph_fake()
    result = agent.run("What is the status of invoice INV-42?")
    assert result.answer == ANSWER and (result.tokens_in, result.tokens_out) == (31, 8)
    assert _called_get_invoice(ledger)


# --------------------------------------------------------------------------- openai-agents
def _openai_agents_fake() -> Any:
    from agents.items import ModelResponse
    from agents.models.interface import Model
    from agents.usage import Usage
    from openai.types.responses import (
        ResponseFunctionToolCall,
        ResponseOutputMessage,
        ResponseOutputText,
    )

    class Fake(Model):
        turn = 0

        async def get_response(self, *args: Any, **kwargs: Any) -> Any:
            self.turn += 1
            if self.turn == 1:
                out: list[Any] = [
                    ResponseFunctionToolCall(
                        type="function_call",
                        call_id="call_1",
                        name="get_invoice",
                        arguments=json.dumps({"invoice_id": "INV-42"}),
                        id="fc_1",
                        status="completed",
                    )
                ]
            else:
                out = [
                    ResponseOutputMessage(
                        id="msg_1",
                        type="message",
                        role="assistant",
                        status="completed",
                        content=[
                            ResponseOutputText(type="output_text", text=ANSWER, annotations=[])
                        ],
                    )
                ]
            return ModelResponse(
                output=out,
                usage=Usage(requests=1, input_tokens=10, output_tokens=2, total_tokens=12),
                response_id=None,
            )

        def stream_response(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
            raise NotImplementedError

    return Fake()


@pytest.mark.parametrize("config_label", CONFIGS)
def test_openai_agents_loop(tmp_path: Path, config_label: str) -> None:
    agent, ledger = _agent("openai-agents", tmp_path, config_label)
    agent.model_override = _openai_agents_fake()
    result = agent.run("What is the status of invoice INV-42?")
    assert result.answer == ANSWER and (result.tokens_in, result.tokens_out) == (20, 4)
    assert _called_get_invoice(ledger)


# --------------------------------------------------------------------------- pydantic-ai
def _pydantic_ai_fake() -> Any:
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
    from pydantic_ai.models.function import FunctionModel

    def fn(messages: list[Any], info: Any) -> Any:
        returned = any(
            isinstance(p, ToolReturnPart) for m in messages for p in getattr(m, "parts", [])
        )
        if returned:
            return ModelResponse(parts=[TextPart(ANSWER)])
        return ModelResponse(parts=[ToolCallPart("get_invoice", {"invoice_id": "INV-42"})])

    return FunctionModel(fn)


@pytest.mark.parametrize("config_label", CONFIGS)
def test_pydantic_ai_loop(tmp_path: Path, config_label: str) -> None:
    agent, ledger = _agent("pydantic-ai", tmp_path, config_label)
    agent.model_override = _pydantic_ai_fake()
    result = agent.run("What is the status of invoice INV-42?")
    assert result.answer == ANSWER and result.tokens_in > 0 and result.tokens_out > 0
    assert _called_get_invoice(ledger)


# --------------------------------------------------------------------------- smolagents
def _smolagents_fake() -> Any:
    from smolagents.models import (
        ChatMessage,
        ChatMessageToolCall,
        ChatMessageToolCallFunction,
        MessageRole,
        Model,
    )
    from smolagents.monitoring import TokenUsage

    class Fake(Model):
        turn = 0

        def generate(
            self,
            messages: Any,
            stop_sequences: Any = None,
            response_format: Any = None,
            tools_to_call_from: Any = None,
            **kwargs: Any,
        ) -> Any:
            self.turn += 1
            name, args = (
                ("get_invoice", {"invoice_id": "INV-42"})
                if self.turn == 1
                else ("final_answer", {"answer": ANSWER})
            )
            return ChatMessage(
                role=MessageRole.ASSISTANT,
                content="",
                tool_calls=[
                    ChatMessageToolCall(
                        id=f"call_{self.turn}",
                        type="function",
                        function=ChatMessageToolCallFunction(name=name, arguments=args),
                    )
                ],
                token_usage=TokenUsage(input_tokens=10, output_tokens=2),
            )

    return Fake(model_id="fake")


@pytest.mark.parametrize("config_label", CONFIGS)
def test_smolagents_loop(tmp_path: Path, config_label: str) -> None:
    agent, ledger = _agent("smolagents", tmp_path, config_label)
    agent.model_override = _smolagents_fake()
    result = agent.run("What is the status of invoice INV-42?")
    assert result.answer == ANSWER and (result.tokens_in, result.tokens_out) == (20, 4)
    assert _called_get_invoice(ledger)


# --------------------------------------------------------------------------- crashes
def test_a_framework_error_becomes_agent_crash(tmp_path: Path) -> None:
    agent, _ = _agent("pydantic-ai", tmp_path, "default")
    from pydantic_ai.models.function import FunctionModel

    def broken(messages: list[Any], info: Any) -> Any:
        raise RuntimeError("model exploded")

    agent.model_override = FunctionModel(broken)
    with pytest.raises(AgentCrash, match="model exploded"):
        agent.run("What is the status of invoice INV-42?")
