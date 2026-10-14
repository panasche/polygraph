"""JUnit XML for CI systems: one ``<testcase>`` per run.

Bad outcomes (silent lie, double action, hijacked, loop) become ``<failure>``,
a crash becomes ``<error>``, everything else passes. Payload text is never
written here.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET  # noqa: S405 - we only build XML, never parse it here

from ..results import BAD_OUTCOMES, Outcome, ResultsFile, Run

# Characters XML 1.0 does not allow, even escaped.
_INVALID_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def _clean(text: str) -> str:
    return _INVALID_XML.sub("�", text)


def split_test_id(test_id: str) -> tuple[str, str]:
    """Split a pytest node id into a JUnit ``(classname, name)`` pair.

    ``tests/agents/test_bank.py::TestPay::test_retry[x]`` gives
    ``("tests.agents.test_bank", "TestPay::test_retry[x]")``.
    """
    module, sep, rest = test_id.partition("::")
    if not sep:
        return "polygraph", test_id
    module = module.replace("\\", "/")
    if module.endswith(".py"):
        module = module[: -len(".py")]
    classname = ".".join(part for part in module.split("/") if part and part != ".")
    return classname or "polygraph", rest


def _case_name(run: Run) -> str:
    _, name = split_test_id(run.test_id)
    fault = run.fault
    if fault is None:
        tag = "[control]"
    else:
        call = f" call {fault.on_call}" if fault.on_call != 1 else ""
        tag = f"[{fault.name.value} {fault.server}/{fault.tool}{call}]"
    return f"{name} {tag} #{run.repeat}"


def _body(run: Run) -> str:
    lines = list(run.reasons)
    if run.error:
        lines.append(f"error: {run.error}")
    lines.append(f"run id: {run.id}")
    return "\n".join(lines)


def _message(run: Run) -> str:
    first = run.reasons[0] if run.reasons else (run.error or "")
    msg = f"{run.outcome.value}: {first}" if first else run.outcome.value
    return msg if len(msg) <= 500 else msg[:497] + "..."


def render_junit(results: ResultsFile) -> str:
    """Render the results as a JUnit XML document (a string, UTF-8 declared)."""
    runs = results.runs
    failures = sum(1 for r in runs if r.outcome in BAD_OUTCOMES)
    errors = sum(1 for r in runs if r.outcome is Outcome.CRASH)
    total_s = sum(r.duration_ms for r in runs) / 1000

    root = ET.Element("testsuites", name="polygraph")
    root.set("tests", str(len(runs)))
    root.set("failures", str(failures))
    root.set("errors", str(errors))
    root.set("time", f"{total_s:.3f}")

    suite = ET.SubElement(root, "testsuite", name="polygraph")
    suite.set("tests", str(len(runs)))
    suite.set("failures", str(failures))
    suite.set("errors", str(errors))
    suite.set("skipped", "0")
    suite.set("time", f"{total_s:.3f}")
    suite.set("timestamp", results.generated_at.isoformat())

    env = results.environment
    suite_props = ET.SubElement(suite, "properties")
    for key, value in (
        ("polygraph_version", env.polygraph_version),
        ("seed", str(env.seed)),
        ("content_included", str(results.content_included).lower()),
        ("git_sha", env.git.sha),
        ("git_branch", env.git.branch),
    ):
        if value is not None:
            ET.SubElement(suite_props, "property", name=key, value=_clean(value))

    for run in runs:
        classname, _ = split_test_id(run.test_id)
        case = ET.SubElement(suite, "testcase")
        case.set("classname", _clean(classname))
        case.set("name", _clean(_case_name(run)))
        case.set("time", f"{run.duration_ms / 1000:.3f}")

        props = ET.SubElement(case, "properties")
        fault = run.fault
        fault_text = (
            "control" if fault is None else f"{fault.name.value} {fault.server}/{fault.tool}"
        )
        for key, value in (
            ("fault", fault_text),
            ("seed", str(run.seed)),
            ("outcome", run.outcome.value),
            ("run_id", run.id),
            ("repeat", str(run.repeat)),
        ):
            ET.SubElement(props, "property", name=key, value=_clean(value))

        if run.outcome in BAD_OUTCOMES:
            tag = "failure"
        elif run.outcome is Outcome.CRASH:
            tag = "error"
        else:
            continue
        problem = ET.SubElement(case, tag)
        problem.set("message", _clean(_message(run)))
        problem.set("type", f"polygraph.{run.outcome.value}")
        problem.text = _clean(_body(run))

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"
