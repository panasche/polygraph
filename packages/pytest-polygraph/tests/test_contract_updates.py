"""Additive contract changes the other components rely on."""

from __future__ import annotations

import re
from pathlib import Path

import polygraph
from polygraph.report.markdown import render_pr_comment

RATE = {"numerator": 1, "denominator": 10, "value": 0.1, "ci_low": 0.02, "ci_high": 0.4}


def test_compare_pairs_may_have_null_sides():
    lite = {
        "branch": "main",
        "success": RATE,
        "silent_lie": RATE,
        "double_action": RATE,
        "hijack": RATE,
        "coverage": {"applicable": 30, "covered": 3},
    }
    compare = {
        "base": lite,
        "head": lite,
        "metrics": {},
        "coverage": {},
        "pairs": [
            {
                "server": "bank",
                "tool": "pay_invoice",
                "fault": "lost_response",
                "base": None,
                "head": {"runs": 2, "bad": 1},
            },
            {
                "server": "bank",
                "tool": "refund",
                "fault": "duplicate_delivery",
                "base": {"runs": 2, "bad": 0},
                "head": None,
            },
        ],
    }
    md = render_pr_comment(None, compare=compare)
    assert md.startswith("<!-- polygraph -->")
    assert "pay_invoice" in md


def test_version_is_a_plain_semver_string():
    # release.yml compares this with the git tag (without the leading "v").
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:[ab]\d+|rc\d+)?", polygraph.__version__)
    init = Path(polygraph.__file__).read_text(encoding="utf-8")
    assert f'__version__ = "{polygraph.__version__}"' in init


def test_dogfood_example_suite_exists():
    root = Path(__file__).resolve().parents[1] / "examples" / "bank"
    assert (root / "pyproject.toml").exists() and (root / "test_bank.py").exists()
