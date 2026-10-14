"""The 13 faults in 4 families. See each module for what the agent sees.

| family      | faults                                                        |
|-------------|---------------------------------------------------------------|
| unavailable | timeout, slow, error, rate_limit, auth_expired                |
| divergent   | lost_response, duplicate_delivery                             |
| corrupt     | partial, malformed, schema_drift, stale (+ conflict variant)  |
| hostile     | injection, description_poison (static or rug_pull)           |
"""

from __future__ import annotations

from ..results import FaultName
from .base import CallResult, Fault, FaultContext, FaultPlan, ToolCall, Upstream
from .corrupt import Malformed, Partial, SchemaDrift, Stale
from .divergent import DuplicateDelivery, LostResponse
from .hostile import DescriptionPoison, Injection
from .unavailable import AuthExpired, Error, RateLimit, Slow, Timeout

FAULTS: dict[FaultName, type[Fault]] = {
    cls.name: cls
    for cls in (
        Timeout,
        Slow,
        Error,
        RateLimit,
        AuthExpired,
        LostResponse,
        DuplicateDelivery,
        Partial,
        Malformed,
        SchemaDrift,
        Stale,
        Injection,
        DescriptionPoison,
    )
}

#: One plain sentence per fault, used by ``polygraph plan`` and the README.
DESCRIPTIONS: dict[FaultName, str] = {
    FaultName.TIMEOUT: "No reply; after timeout_s the agent gets a timeout error",
    FaultName.SLOW: "Correct reply, but very late",
    FaultName.ERROR: "A generic 'something went wrong' tool error",
    FaultName.RATE_LIMIT: "'Too many requests, retry in N s'",
    FaultName.AUTH_EXPIRED: "'Your login expired' tool error",
    FaultName.LOST_RESPONSE: "The server does the work, the agent gets an error",
    FaultName.DUPLICATE_DELIVERY: "The server gets the request twice",
    FaultName.PARTIAL: "Half the data, no warning",
    FaultName.MALFORMED: "Broken JSON or wrong types",
    FaultName.SCHEMA_DRIFT: "A field got renamed",
    FaultName.STALE: "An old version of the record (or 'not found' with variant=conflict)",
    FaultName.INJECTION: "Normal data with a hidden instruction inside",
    FaultName.DESCRIPTION_POISON: "The tool description holds an instruction (static or rug pull)",
}


def make_fault(ctx: FaultContext) -> Fault:
    return FAULTS[ctx.spec.name](ctx)


__all__ = [
    "DESCRIPTIONS",
    "FAULTS",
    "CallResult",
    "Fault",
    "FaultContext",
    "FaultPlan",
    "ToolCall",
    "Upstream",
    "make_fault",
]
