"""What the current pytest session benchmarks, read from environment variables.

``run.py`` sets these for each (framework, config, model) cell. Running
``pytest tasks/`` by hand works too: set them yourself.

=================================  ===========================================
``POLYGRAPH_BENCH_FRAMEWORK``      adapter name, e.g. ``langgraph`` (dry run:
                                   ``scripted-naive`` / ``scripted-careful``)
``POLYGRAPH_BENCH_CONFIG``         config label, e.g. ``default`` or ``R2``
``POLYGRAPH_BENCH_MODEL``          ``provider:model-id`` (dry run: ``scripted``)
``POLYGRAPH_BENCH_CALL_TIMEOUT``   seconds the scripted agents wait per call
=================================  ===========================================

The matching Polygraph labels (``POLYGRAPH_FRAMEWORK``, ``_FRAMEWORK_VERSION``,
``_MODEL``, ``_CONFIG_LABEL``) are filled from these so every results file says
exactly what produced it.
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from dataclasses import dataclass

from . import models

FRAMEWORK_ENV = "POLYGRAPH_BENCH_FRAMEWORK"
CONFIG_ENV = "POLYGRAPH_BENCH_CONFIG"
CALL_TIMEOUT_ENV = "POLYGRAPH_BENCH_CALL_TIMEOUT"


@dataclass(frozen=True)
class Settings:
    framework: str
    config_label: str
    model: models.ModelSpec
    call_timeout: float = 30.0


def current(env: MutableMapping[str, str] | None = None) -> Settings:
    e = env if env is not None else os.environ
    framework = e.get(FRAMEWORK_ENV, "").strip()
    if not framework:
        raise RuntimeError(
            f"{FRAMEWORK_ENV} is not set. Use run.py, or set it (and "
            f"{models.MODEL_ENV}) before running pytest tasks/."
        )
    model = models.parse(e.get(models.MODEL_ENV) or models.SCRIPTED)
    try:
        timeout = float(e.get(CALL_TIMEOUT_ENV, "30"))
    except ValueError:
        timeout = 30.0
    return Settings(
        framework=framework,
        config_label=e.get(CONFIG_ENV, "default").strip() or "default",
        model=model,
        call_timeout=timeout,
    )


def export_labels(settings: Settings, version: str | None, env: MutableMapping[str, str]) -> None:
    """Fill Polygraph's results-file labels (only those not set already)."""
    env.setdefault("POLYGRAPH_FRAMEWORK", settings.framework)
    if version:
        env.setdefault("POLYGRAPH_FRAMEWORK_VERSION", version)
    env.setdefault("POLYGRAPH_MODEL", str(settings.model))
    env.setdefault("POLYGRAPH_CONFIG_LABEL", settings.config_label)
