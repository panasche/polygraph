"""The ``polygraph-results/v1`` file format.

One versioned JSON file feeds everything: the local HTML report, JUnit, the PR
comment, the badge and the cloud upload. The cloud API validates uploads with
these exact models, so writer and reader cannot drift apart.

Rules for changing this file:
- Additive, optional fields are fine within v1.
- Renaming, removing or changing the meaning of a field means a new schema id
  (``polygraph-results/v2``) and a reader for the old one.

This module must stay importable with only ``pydantic`` installed. The cloud
service imports it without pytest or the proxy.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_ID = "polygraph-results/v1"

# Hard limits shared by writer and cloud. The cloud may enforce lower ones.
MAX_RUNS_PER_FILE = 5_000
MAX_LEDGER_ENTRIES_PER_RUN = 500
MAX_PAYLOAD_TEXT_BYTES = 64 * 1024


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False, populate_by_name=True)


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #


class FaultFamily(str, Enum):
    UNAVAILABLE = "unavailable"  # the tool is down or slow
    DIVERGENT = "divergent"  # the tool did something different from what it said
    CORRUPT = "corrupt"  # the tool's data is wrong
    HOSTILE = "hostile"  # the tool is hostile


class FaultName(str, Enum):
    TIMEOUT = "timeout"
    SLOW = "slow"
    ERROR = "error"
    RATE_LIMIT = "rate_limit"
    AUTH_EXPIRED = "auth_expired"
    LOST_RESPONSE = "lost_response"
    DUPLICATE_DELIVERY = "duplicate_delivery"
    PARTIAL = "partial"
    MALFORMED = "malformed"
    SCHEMA_DRIFT = "schema_drift"
    STALE = "stale"  # params.variant = "conflict" for the two-tools-disagree case
    INJECTION = "injection"
    DESCRIPTION_POISON = "description_poison"  # params.mode = "static" | "rug_pull"


FAULT_FAMILY: dict[FaultName, FaultFamily] = {
    FaultName.TIMEOUT: FaultFamily.UNAVAILABLE,
    FaultName.SLOW: FaultFamily.UNAVAILABLE,
    FaultName.ERROR: FaultFamily.UNAVAILABLE,
    FaultName.RATE_LIMIT: FaultFamily.UNAVAILABLE,
    FaultName.AUTH_EXPIRED: FaultFamily.UNAVAILABLE,
    FaultName.LOST_RESPONSE: FaultFamily.DIVERGENT,
    FaultName.DUPLICATE_DELIVERY: FaultFamily.DIVERGENT,
    FaultName.PARTIAL: FaultFamily.CORRUPT,
    FaultName.MALFORMED: FaultFamily.CORRUPT,
    FaultName.SCHEMA_DRIFT: FaultFamily.CORRUPT,
    FaultName.STALE: FaultFamily.CORRUPT,
    FaultName.INJECTION: FaultFamily.HOSTILE,
    FaultName.DESCRIPTION_POISON: FaultFamily.HOSTILE,
}


class Outcome(str, Enum):
    RECOVERED = "recovered"  # task done correctly despite the fault
    HONEST_FAIL = "honest_fail"  # didn't finish, told the truth
    SILENT_LIE = "silent_lie"  # said "done", the state says no
    DOUBLE_ACTION = "double_action"  # did a one-time action twice
    HIJACKED = "hijacked"  # followed an injected or poisoned instruction
    LOOP = "loop"  # same call 3+ times in a row
    CRASH = "crash"  # the agent itself threw


#: Outcomes that count as a failure of honesty or safety. Used by
#: ``assert_honest`` and by the badge colour.
BAD_OUTCOMES = frozenset(
    {Outcome.SILENT_LIE, Outcome.DOUBLE_ACTION, Outcome.HIJACKED, Outcome.LOOP}
)

#: Precedence when several outcomes apply to one run. Higher wins.
OUTCOME_PRECEDENCE: dict[Outcome, int] = {
    Outcome.HIJACKED: 70,
    Outcome.DOUBLE_ACTION: 60,
    Outcome.SILENT_LIE: 50,
    Outcome.CRASH: 40,
    Outcome.LOOP: 30,
    Outcome.HONEST_FAIL: 20,
    Outcome.RECOVERED: 10,
}


class LabelSource(str, Enum):
    OVERRIDE = "override"  # pyproject.toml [tool.polygraph.overrides]
    SERVER = "server"  # the tool's MCP annotations
    GUESSED = "guessed"  # guessed from the tool name
    DEFAULT = "default"  # MCP spec default (assumes the worst)


class Priority(str, Enum):
    TOP = "top"
    NORMAL = "normal"


# --------------------------------------------------------------------------- #
# Building blocks
# --------------------------------------------------------------------------- #


class Payload(_Model):
    """Some content (tool arguments, a tool result, the agent's answer).

    ``sha256`` and ``size`` are always present. ``text`` is present only when
    the writer was configured to include content (it is off for uploads unless
    the project opted in, and then only after the user's mask ran).
    """

    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    size: Annotated[int, Field(ge=0)]
    text: str | None = None

    @classmethod
    def of(cls, content: str | bytes | Any, *, include_text: bool = True) -> "Payload":
        if isinstance(content, bytes):
            raw = content
        elif isinstance(content, str):
            raw = content.encode("utf-8")
        else:
            import json

            raw = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str).encode()
        text = raw.decode("utf-8", errors="replace") if include_text else None
        if text is not None and len(raw) > MAX_PAYLOAD_TEXT_BYTES:
            text = raw[:MAX_PAYLOAD_TEXT_BYTES].decode("utf-8", errors="ignore") + "…[truncated]"
        return cls(sha256=hashlib.sha256(raw).hexdigest(), size=len(raw), text=text)

    def without_text(self) -> "Payload":
        return self.model_copy(update={"text": None})


class Rate(_Model):
    """A proportion with a 95 % Wilson score interval."""

    numerator: Annotated[int, Field(ge=0)]
    denominator: Annotated[int, Field(ge=0)]
    value: Annotated[float, Field(ge=0, le=1)] | None = None  # None when denominator == 0
    ci_low: Annotated[float, Field(ge=0, le=1)] | None = None
    ci_high: Annotated[float, Field(ge=0, le=1)] | None = None

    @model_validator(mode="after")
    def _check(self) -> "Rate":
        if self.numerator > self.denominator:
            raise ValueError("numerator cannot exceed denominator")
        return self


class HintValue(_Model):
    value: bool
    source: LabelSource


class ToolHints(_Model):
    read_only: HintValue
    destructive: HintValue
    idempotent: HintValue
    open_world: HintValue


class ToolInfo(_Model):
    server: str
    name: str
    hints: ToolHints
    description_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    has_output_schema: bool = False

    @property
    def key(self) -> str:
        return f"{self.server}/{self.name}"


class FaultSpec(_Model):
    name: FaultName
    server: str
    tool: str
    on_call: Annotated[int, Field(ge=1)] = 1  # which call to this tool gets the fault
    params: dict[str, Any] = Field(default_factory=dict)

    @property
    def family(self) -> FaultFamily:
        return FAULT_FAMILY[self.name]


class CoveragePair(_Model):
    server: str
    tool: str
    fault: FaultName
    priority: Priority = Priority.NORMAL
    runs_required: Annotated[int, Field(ge=1)]
    runs_done: Annotated[int, Field(ge=0)] = 0

    @property
    def covered(self) -> bool:
        return self.runs_done >= self.runs_required


class Coverage(_Model):
    applicable: Annotated[int, Field(ge=0)]
    covered: Annotated[int, Field(ge=0)]
    pairs: list[CoveragePair] = Field(default_factory=list)

    @property
    def ratio(self) -> float | None:
        return None if self.applicable == 0 else self.covered / self.applicable


# --------------------------------------------------------------------------- #
# The ledger: asked · happened · told
# --------------------------------------------------------------------------- #


class Happened(_Model):
    """What really happened on the server for one agent call."""

    forwarded: Annotated[int, Field(ge=0)]  # times the request reached the server (2 = duplicated)
    server_result: Payload | None = None  # what the server actually returned (first delivery)
    server_is_error: bool = False
    note: str | None = None  # e.g. "result withheld from agent (lost_response)"


class LedgerEntry(_Model):
    seq: Annotated[int, Field(ge=0)]
    ts: datetime
    server: str
    method: str  # "tools/call", "tools/list", ...
    tool: str | None = None
    request_id: str | int | None = None
    protocol_version: str | None = None
    fault: FaultName | None = None
    asked: Payload  # what the agent asked for (request params)
    happened: Happened
    told: Payload | None = None  # what the agent received; None = never answered (timeout)
    told_is_error: bool = False
    latency_ms: Annotated[float, Field(ge=0)] = 0.0


class StateCheck(_Model):
    name: str
    passed: bool
    detail: str | None = None


class JudgeVerdict(_Model):
    """Answer to the one narrow question: did the agent *claim* success?"""

    claimed_success: bool | None  # None = judge could not tell
    method: Literal["rules", "llm", "manual"] = "rules"
    model: str | None = None
    rationale: str | None = None


class SecondOpinion(_Model):
    """Optional transcript-only check (e.g. agent-polygraph's ``verify()``)."""

    tool: str
    flagged_lie: bool
    agrees_with_polygraph: bool
    detail: str | None = None


class TokenUsage(_Model):
    input: Annotated[int, Field(ge=0)] = 0
    output: Annotated[int, Field(ge=0)] = 0


class Run(_Model):
    """One execution of one test under one fault (or none, for the control)."""

    id: Annotated[str, Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
    test_id: str  # pytest node id
    task: str | None = None
    fault: FaultSpec | None = None  # None = control run, no fault
    seed: int
    repeat: Annotated[int, Field(ge=0)] = 0
    started_at: datetime
    duration_ms: Annotated[float, Field(ge=0)]
    outcome: Outcome
    reasons: list[str] = Field(default_factory=list)
    judge: JudgeVerdict | None = None
    second_opinion: SecondOpinion | None = None
    final_answer: Payload | None = None
    state_checks: list[StateCheck] = Field(default_factory=list)
    ledger: Annotated[list[LedgerEntry], Field(max_length=MAX_LEDGER_ENTRIES_PER_RUN)] = Field(
        default_factory=list
    )
    tokens: TokenUsage | None = None
    error: str | None = None  # agent exception summary when outcome == crash
    protocol_version: str | None = None


class ClaimCheck(_Model):
    """A check of the *server's* own claims: a polygraph for tools."""

    server: str
    tool: str
    claim: Literal["idempotent", "read_only"]
    held: bool
    evidence: str
    run_id: str | None = None


# --------------------------------------------------------------------------- #
# Summary (derived; the cloud recomputes it and never trusts the client's)
# --------------------------------------------------------------------------- #


class PassK(_Model):
    k: Annotated[int, Field(ge=1)]
    groups: Annotated[int, Field(ge=0)]  # (test, fault) groups with at least k repeats
    passed: Annotated[int, Field(ge=0)]  # groups where all k repeats recovered


class Summary(_Model):
    runs: Annotated[int, Field(ge=0)]
    faulted_runs: Annotated[int, Field(ge=0)]
    outcomes: dict[Outcome, int]
    success: Rate
    silent_lie: Rate
    double_action: Rate
    hijack: Rate
    loop: Rate
    crash: Rate
    pass_k: PassK | None = None
    cost_multiplier: float | None = None  # mean tokens under fault / mean tokens in control
    coverage_ratio: float | None = None


# --------------------------------------------------------------------------- #
# Environment and the file itself
# --------------------------------------------------------------------------- #


class GitInfo(_Model):
    sha: Annotated[str, Field(pattern=r"^[0-9a-f]{7,40}$")] | None = None
    branch: str | None = None
    ref: str | None = None
    pr_number: int | None = None
    base_branch: str | None = None
    repository: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")] | None = None


class Environment(_Model):
    polygraph_version: str
    python_version: str
    platform: str
    ci: str | None = None  # "github-actions", "local", ...
    git: GitInfo = Field(default_factory=GitInfo)
    framework: str | None = None  # e.g. "langgraph"
    framework_version: str | None = None
    config_label: str | None = None  # e.g. "default", "best-practice", "R2"
    model: str | None = None
    protocol_versions: list[str] = Field(default_factory=list)  # MCP versions seen on the wire
    seed: int
    extra: dict[str, str] = Field(default_factory=dict)

    @field_validator("extra")
    @classmethod
    def _small_extra(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > 32 or any(len(k) > 64 or len(x) > 256 for k, x in v.items()):
            raise ValueError("extra is limited to 32 short string pairs")
        return v


class ResultsFile(_Model):
    schema_: Literal["polygraph-results/v1"] = Field(default=SCHEMA_ID, alias="schema")
    generated_at: datetime
    content_included: bool = False  # True if any Payload.text is present
    environment: Environment
    tools: list[ToolInfo] = Field(default_factory=list)
    coverage: Coverage
    runs: Annotated[list[Run], Field(max_length=MAX_RUNS_PER_FILE)] = Field(default_factory=list)
    server_claims: list[ClaimCheck] = Field(default_factory=list)
    summary: Summary

    @model_validator(mode="after")
    def _content_flag_matches(self) -> "ResultsFile":
        if not self.content_included and any(True for _ in iter_payloads(self) if _.text is not None):
            raise ValueError("payload text present but content_included is false")
        ids = [r.id for r in self.runs]
        if len(ids) != len(set(ids)):
            raise ValueError("run ids must be unique")
        return self

    def to_json(self, **kw: Any) -> str:
        return self.model_dump_json(by_alias=True, **kw)

    def stripped(self) -> "ResultsFile":
        """A copy with every payload's text removed (the default for uploads)."""
        data = self.model_copy(deep=True)
        for p in iter_payloads(data):
            p.text = None
        data.content_included = False
        return data


def iter_payloads(results: ResultsFile) -> Iterator[Payload]:
    """Yield every Payload object in a results file (mutable references)."""
    for run in results.runs:
        if run.final_answer is not None:
            yield run.final_answer
        for e in run.ledger:
            yield e.asked
            if e.told is not None:
                yield e.told
            if e.happened.server_result is not None:
                yield e.happened.server_result


def parse(data: str | bytes | dict[str, Any]) -> ResultsFile:
    """Parse and validate a results file, rejecting unknown schema versions."""
    if isinstance(data, (str, bytes)):
        return ResultsFile.model_validate_json(data)
    return ResultsFile.model_validate(data)


def json_schema() -> dict[str, Any]:
    return ResultsFile.model_json_schema(by_alias=True)
