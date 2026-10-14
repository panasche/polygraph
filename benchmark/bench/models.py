"""Model specs: ``provider:model-id``, read from the environment.

The model under test comes from ``POLYGRAPH_BENCH_MODEL`` (or ``--model``), for
example ``openai:<model-id>`` or ``anthropic:<model-id>``. API keys are only ever
read from the provider's usual environment variable; nothing is stored in files.

Each adapter reaches the provider the way its framework does by default. Where a
framework has no native client for a provider it uses that provider's
OpenAI-compatible endpoint; the README lists which API each pair uses.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

MODEL_ENV = "POLYGRAPH_BENCH_MODEL"
JUDGE_MODEL_ENV = "POLYGRAPH_BENCH_JUDGE_MODEL"

#: provider -> environment variable that holds its API key
KEY_ENV: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}
#: OpenAI-compatible endpoints, for frameworks without a native client for the provider
OPENAI_COMPATIBLE_BASE_URL: dict[str, str] = {
    "anthropic": "https://api.anthropic.com/v1/",
}
#: the dry-run "model": scripted agents, no LLM
SCRIPTED = "scripted"

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    name: str

    def __str__(self) -> str:
        return self.provider if self.provider == SCRIPTED else f"{self.provider}:{self.name}"

    @property
    def is_scripted(self) -> bool:
        return self.provider == SCRIPTED

    @property
    def key_env(self) -> str | None:
        return KEY_ENV.get(self.provider)

    @property
    def slug(self) -> str:
        """Safe for file names: ``openai_gpt-x`` style."""
        return _SAFE.sub("-", str(self).replace(":", "_")).strip("-") or "model"

    def api_key(self, env: Mapping[str, str] | None = None) -> str | None:
        e = env if env is not None else os.environ
        var = self.key_env
        return e.get(var) if var else None


def parse(spec: str | ModelSpec | None) -> ModelSpec:
    if isinstance(spec, ModelSpec):
        return spec
    raw = (spec or "").strip()
    if not raw:
        raise ValueError(f"no model given: set {MODEL_ENV}=provider:model-id or pass --model")
    if raw == SCRIPTED:
        return ModelSpec(SCRIPTED, SCRIPTED)
    provider, sep, name = raw.partition(":")
    if not sep or not provider or not name:
        raise ValueError(f"model must look like 'provider:model-id', got {raw!r}")
    provider = provider.lower()
    if provider not in KEY_ENV:
        raise ValueError(f"unsupported provider {provider!r}; supported: {', '.join(KEY_ENV)}")
    return ModelSpec(provider, name)


def from_env(env: Mapping[str, str] | None = None, var: str = MODEL_ENV) -> ModelSpec:
    e = env if env is not None else os.environ
    return parse(e.get(var))


def missing_key(spec: ModelSpec, env: Mapping[str, str] | None = None) -> str | None:
    """The env var name that must be set but is not (``None`` when all is well)."""
    if spec.is_scripted:
        return None
    return None if spec.api_key(env) else spec.key_env
