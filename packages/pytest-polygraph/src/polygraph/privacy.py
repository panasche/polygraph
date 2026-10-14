"""Privacy controls applied to a results file before it leaves the machine.

Two modes:

- **Default (no content):** :func:`strip_free_text` removes every payload text
  plus the free-text fields that may quote payloads (state-check details, judge
  rationales, second-opinion details, exception messages).
- **Opt-in content:** :func:`apply_mask` runs the project's mask callable over
  every text that could carry user data. It fails closed: one failure and
  nothing is returned (and so nothing is uploaded).

Masking never changes ``Payload.sha256`` or ``Payload.size``. Those describe the
*original* content (so runs stay comparable across uploads) and reveal nothing
beyond a length and a one-way hash.

This module imports only the standard library and ``polygraph.results``.
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Callable
from typing import Any

from .results import ResultsFile, iter_payloads, parse

__all__ = [
    "MaskError",
    "apply_mask",
    "load_callable",
    "prepare_for_upload",
    "strip_free_text",
]

Mask = Callable[[str], str]

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


class MaskError(RuntimeError):
    """The mask could not be loaded, raised, or returned something other than ``str``.

    Messages name the field being masked, never the text itself.
    """


def load_callable(spec: str) -> Callable[..., Any]:
    """Load ``"module.path:attr"`` (``attr`` may be dotted, e.g. ``"pkg.mod:Cls.method"``)."""
    if not isinstance(spec, str) or spec.count(":") != 1:
        raise ValueError(f"Expected 'module.path:attribute', got {spec!r}")
    module_name, attr_path = (part.strip() for part in spec.split(":"))
    if not _IDENT.match(module_name) or not _IDENT.match(attr_path):
        raise ValueError(f"Expected 'module.path:attribute', got {spec!r}")
    try:
        obj: Any = importlib.import_module(module_name)
    except ImportError as exc:
        raise ImportError(f"Cannot import module {module_name!r} (from {spec!r}): {exc}") from exc
    for part in attr_path.split("."):
        try:
            obj = getattr(obj, part)
        except AttributeError as exc:
            raise AttributeError(f"{spec!r}: {part!r} not found") from exc
    if not callable(obj):
        raise TypeError(f"{spec!r} is not callable")
    return obj  # type: ignore[no-any-return]


def _masked(mask: Mask, text: str, where: str) -> str:
    try:
        out = mask(text)
    except Exception as exc:
        # Deliberately no text in the message; the cause is chained for debugging.
        raise MaskError(f"mask raised {type(exc).__name__} on {where}") from exc
    if not isinstance(out, str):
        raise MaskError(f"mask returned {type(out).__name__} instead of str on {where}")
    return out


def _has_text(results: ResultsFile) -> bool:
    return any(p.text is not None for p in iter_payloads(results))


def apply_mask(results: ResultsFile, mask: Mask) -> ResultsFile:
    """Return a deep copy with ``mask`` applied to every payload text and free-text field.

    Fields covered: every ``Payload.text``, ``Run.task``, ``Run.error``, ``Run.reasons``,
    ``StateCheck.detail``, ``JudgeVerdict.rationale``, ``SecondOpinion.detail``,
    ``Happened.note`` and ``ClaimCheck.evidence``. ``Payload.sha256``/``size`` keep
    describing the original content. The input is never modified; on any mask failure
    :class:`MaskError` is raised and no copy is returned.
    """
    data = results.model_copy(deep=True)

    def m(text: str | None, where: str) -> str | None:
        return None if text is None else _masked(mask, text, where)

    for i, run in enumerate(data.runs):
        where = f"runs[{i}]"
        run.task = m(run.task, f"{where}.task")
        run.error = m(run.error, f"{where}.error")
        run.reasons = [_masked(mask, r, f"{where}.reasons[{j}]") for j, r in enumerate(run.reasons)]
        for j, check in enumerate(run.state_checks):
            check.detail = m(check.detail, f"{where}.state_checks[{j}].detail")
        if run.judge is not None:
            run.judge.rationale = m(run.judge.rationale, f"{where}.judge.rationale")
        if run.second_opinion is not None:
            run.second_opinion.detail = m(
                run.second_opinion.detail, f"{where}.second_opinion.detail"
            )
        for j, entry in enumerate(run.ledger):
            entry.happened.note = m(entry.happened.note, f"{where}.ledger[{j}].happened.note")
    for i, claim in enumerate(data.server_claims):
        claim.evidence = _masked(mask, claim.evidence, f"server_claims[{i}].evidence")

    # Payload texts: walk with the schema's own iterator so no payload field is missed.
    for k, payload in enumerate(iter_payloads(data)):
        payload.text = m(payload.text, f"payload[{k}].text")

    data.content_included = _has_text(data)
    return data


def _error_type(error: str) -> str:
    """``"ValueError: invoice INV-42 ..."`` → ``"ValueError"``.

    If the part before the first ``:`` does not look like a (dotted) exception type
    name, it might be message text, so it is replaced with ``"Exception"``.
    """
    head = error.split(":", 1)[0].strip()
    return head if _IDENT.match(head) else "Exception"


def strip_free_text(results: ResultsFile) -> ResultsFile:
    """``results.stripped()`` plus removal of free text that may quote payloads.

    Clears ``StateCheck.detail``, ``JudgeVerdict.rationale`` and ``SecondOpinion.detail``
    and reduces ``Run.error`` to the exception type name. ``reasons``, ``Happened.note``,
    ``ClaimCheck.evidence`` and ``task`` are kept: Polygraph writes them without
    payload text.
    """
    data = results.stripped()
    for run in data.runs:
        for check in run.state_checks:
            check.detail = None
        if run.judge is not None:
            run.judge.rationale = None
        if run.second_opinion is not None:
            run.second_opinion.detail = None
        if run.error is not None:
            run.error = _error_type(run.error)
    return data


def prepare_for_upload(
    results: ResultsFile,
    *,
    include_content: bool = False,
    mask: str | Mask | None = None,
) -> ResultsFile:
    """Return the exact object that may be uploaded.

    Without ``include_content`` the result is always :func:`strip_free_text`, even if
    the file contains text. With ``include_content`` a mask is required (a callable or a
    ``"module:attr"`` spec) and every text goes through it. Fails closed with
    :class:`MaskError`. The result is re-validated against the results schema.
    """
    if not include_content:
        prepared = strip_free_text(results)
    else:
        if mask is None:
            raise MaskError(
                "--include-content needs a mask: set [tool.polygraph] mask = 'module:callable'. "
                "Refusing to upload unmasked text."
            )
        if isinstance(mask, str):
            try:
                fn: Mask = load_callable(mask)
            except (ValueError, ImportError, AttributeError, TypeError) as exc:
                raise MaskError(f"could not load mask {mask!r}: {exc}") from exc
        else:
            fn = mask
        if not callable(fn):
            raise MaskError("mask is not callable")
        prepared = apply_mask(results, fn)
        prepared.content_included = _has_text(prepared)
    try:
        return parse(prepared.to_json())
    except ValueError as exc:  # pydantic.ValidationError subclasses ValueError
        # Do not echo pydantic's message: it can include the offending input values.
        raise MaskError(
            f"prepared results failed schema validation ({type(exc).__name__})"
        ) from exc
