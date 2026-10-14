"""``[tool.polygraph]`` in ``pyproject.toml``.

.. code-block:: toml

    [tool.polygraph]
    runs = 5
    seed = 1234                 # optional
    loop_threshold = 3
    timeout_s = 30              # how long the proxy holds a "timeout" before giving up the call
    slow_ms = 8000
    judge = "rules"             # or "module.path:callable"
    mask = "module.path:mask"   # optional; runs on every payload text before upload (fail closed)

    [tool.polygraph.servers]
    bank = { command = ["python", "-m", "polygraph.worlds.bank"] }
    shop = { url = "http://localhost:8001/mcp" }

    [tool.polygraph.overrides]
    refund = { read_only = false, idempotent = false }   # key "tool" or "server/tool"

    [tool.polygraph.faults.rate_limit]
    retry_after_s = 10
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .results import FaultName

PYPROJECT = "pyproject.toml"
PYTHON_NAMES = {"python", "python3", "python.exe", "python3.exe", "py"}


def resolve_command(command: list[str]) -> list[str]:
    """Run a bare ``python`` in a server command with the current interpreter.

    On Windows a virtualenv's ``python.exe`` re-launches the base interpreter, so
    a child process asking for plain ``python`` would otherwise get the system
    Python without your packages. An absolute path is left alone.
    """
    import sys

    if command and command[0].lower() in PYTHON_NAMES:
        return [sys.executable, *command[1:]]
    return list(command)


class ConfigError(ValueError):
    pass


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: list[str] | None = None
    url: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _one_transport(self) -> ServerConfig:
        if (self.command is None) == (self.url is None):
            raise ValueError("a server needs exactly one of 'command' (stdio) or 'url' (HTTP)")
        if self.command is not None and not self.command:
            raise ValueError("'command' must not be empty")
        return self

    @property
    def transport(self) -> str:
        return "stdio" if self.command is not None else "http"


class HintOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    read_only: bool | None = None
    destructive: bool | None = None
    idempotent: bool | None = None
    open_world: bool | None = None


class PolygraphConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runs: int = Field(default=1, ge=1)
    seed: int | None = None
    loop_threshold: int = Field(default=3, ge=2)
    timeout_s: float = Field(default=30.0, gt=0)
    slow_ms: int = Field(default=8000, ge=0)
    judge: str = "rules"
    mask: str | None = None
    servers: dict[str, ServerConfig] = Field(default_factory=dict)
    overrides: dict[str, HintOverride] = Field(default_factory=dict)
    faults: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # Optional labels for the results file (also settable via POLYGRAPH_* env vars).
    framework: str | None = None
    framework_version: str | None = None
    model: str | None = None
    config_label: str | None = None

    #: directory of the pyproject.toml the config came from (not a TOML key)
    root: Path = Field(default_factory=Path.cwd, exclude=True)

    @field_validator("faults")
    @classmethod
    def _known_faults(cls, v: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        known = {f.value for f in FaultName}
        bad = sorted(set(v) - known)
        if bad:
            raise ValueError(f"unknown fault(s) in [tool.polygraph.faults]: {', '.join(bad)}")
        return v

    @field_validator("judge")
    @classmethod
    def _judge_spec(cls, v: str) -> str:
        if v != "rules" and ":" not in v:
            raise ValueError("judge must be 'rules' or 'module.path:callable'")
        return v

    def override_for(self, server: str, tool: str) -> HintOverride | None:
        return self.overrides.get(f"{server}/{tool}") or self.overrides.get(tool)

    def labels(self) -> dict[str, str | None]:
        env = os.environ
        return {
            "framework": env.get("POLYGRAPH_FRAMEWORK") or self.framework,
            "framework_version": env.get("POLYGRAPH_FRAMEWORK_VERSION") or self.framework_version,
            "model": env.get("POLYGRAPH_MODEL") or self.model,
            "config_label": env.get("POLYGRAPH_CONFIG_LABEL") or self.config_label,
        }


def find_pyproject(start: Path | None = None) -> Path | None:
    cur = (start or Path.cwd()).resolve()
    for d in (cur, *cur.parents):
        p = d / PYPROJECT
        if p.is_file():
            return p
    return None


def load_config(
    path: str | os.PathLike[str] | None = None, *, start: Path | None = None
) -> PolygraphConfig:
    """Load ``[tool.polygraph]`` from ``path`` or the nearest ``pyproject.toml``.

    Missing file or table means defaults. Invalid values raise :class:`ConfigError`
    with a message that names the bad key.
    """
    p = Path(path) if path else find_pyproject(start)
    if p is None or not p.exists():
        return PolygraphConfig(root=(start or Path.cwd()).resolve())
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{p}: {exc}") from exc
    table = data.get("tool", {}).get("polygraph", {})
    try:
        return PolygraphConfig.model_validate({**table, "root": p.parent.resolve()})
    except ValueError as exc:
        raise ConfigError(f"{p} [tool.polygraph]: {exc}") from exc
