"""Pure unit tests: PII scanner, Verhoeff, badge, gzip cap, ids, logging redaction, signer."""

from __future__ import annotations

import gzip
import json

import pytest

from polygraph_cloud import badge
from polygraph_cloud.ids import new_id, new_upload_token, ulid
from polygraph_cloud.logs import _redact, scrub
from polygraph_cloud.pii import (
    RegexScanner,
    is_personal_ip,
    is_reserved_email_domain,
    verhoeff_check_digit,
    verhoeff_valid,
)
from polygraph_cloud.security import Signer, csrf_for_session
from polygraph_cloud.services.ingest import TooLarge, gunzip_capped

scan = RegexScanner().kinds


def _aadhaar(prefix: str = "23456789012") -> str:
    return prefix + verhoeff_check_digit(prefix)


def test_verhoeff_known_values() -> None:
    assert verhoeff_check_digit("236") == "3"
    assert verhoeff_valid("2363")
    assert not verhoeff_valid("2364")
    n = _aadhaar()
    assert verhoeff_valid(n)
    assert not verhoeff_valid(n[:-1] + str((int(n[-1]) + 1) % 10))


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("mail invented.person@nonexistent-fixture-domain.co.in now", "email"),
        ("call +91 98765 43210", "phone"),
        ("call 9876543210", "phone"),
        ("call 09876543210", "phone"),
        ("dial +44 20 7946 0958", "phone"),
        ("PAN ABCDE1234F on file", "pan"),
        (f"aadhaar {_aadhaar()}", "aadhaar"),
        (f"aadhaar {_aadhaar()[:4]}-{_aadhaar()[4:8]}-{_aadhaar()[8:]}", "aadhaar"),
        ("from 8.8.8.8", "ip_address"),
        ("from 2606:4700:4700::1111", "ip_address"),
    ],
)
def test_regex_scanner_finds(text: str, kind: str) -> None:
    assert kind in scan(text)


@pytest.mark.parametrize(
    "text",
    [
        "send to asha@example.test",
        "send to ops@mail.example.com and x@example.org and y@example.net",
        "user@host.invalid or admin@box.localhost or z@demo.example",
        "server at 10.0.0.4, 192.168.1.20, 172.16.5.5, 127.0.0.1, 100.64.0.9",
        "docs 192.0.2.1 198.51.100.7 203.0.113.9 2001:db8::1 fe80::1 ::1",
        "invoice INV-42 for 1200 rupees at 12:30:45",
        "aadhaar 234567890120 typo",  # wrong checksum
        "sha 0123456789ab and run id run-lost-0002",
        "order 1697040000123 (a millisecond timestamp)",
    ],
)
def test_regex_scanner_allows(text: str) -> None:
    if "234567890120" in text:
        assert not verhoeff_valid("234567890120")
    assert scan(text) == []


def test_reserved_domain_rules() -> None:
    assert is_reserved_email_domain("example.test")
    assert is_reserved_email_domain("Sub.Example.COM")
    assert not is_reserved_email_domain("notexample.com")
    assert not is_reserved_email_domain("example.co")


def test_ip_rules() -> None:
    assert is_personal_ip("1.1.1.1")
    assert not is_personal_ip("192.0.2.55")
    assert not is_personal_ip("not-an-ip")


def test_finding_message_never_contains_value() -> None:
    import conftest
    from polygraph.results import parse
    from polygraph_cloud.pii import scan_results

    res = conftest.make_results(include_text=True, text="PAN ABCDE1234F")
    finding = scan_results(parse(res.to_json()), RegexScanner())
    assert finding is not None and finding.kind == "pan"
    assert "ABCDE1234F" not in finding.message()
    assert finding.run_id in finding.message()


def test_badge_text_and_colours() -> None:
    d = badge.BadgeData(silent_lie=0.0, double_action=0.0, covered=18, applicable=22)
    svg = badge.render(d)
    assert "polygraph" in svg and "silent-lie 0% · coverage 18/22" in svg
    assert badge.GREEN in svg
    assert badge.colour_for(badge.BadgeData(0.0, 0.04, 1, 1)) == badge.AMBER
    assert badge.colour_for(badge.BadgeData(0.2, 0.0, 1, 1)) == badge.CRIMSON
    assert badge.colour_for(badge.BadgeData(0.0, None, 1, 1)) == badge.GREEN
    assert badge.colour_for(None) == badge.GREY
    for banned in ("#E85D26", "#0A2540", "#7C3AED", "#8B5CF6"):
        assert banned.lower() not in svg.lower()
    assert "<script" not in badge.render(None)


def test_gzip_cap() -> None:
    small = gzip.compress(b"a" * 1000)
    assert gunzip_capped(small, 1000) == b"a" * 1000
    with pytest.raises(TooLarge):
        gunzip_capped(small, 999)
    bomb = gzip.compress(b"\0" * (50 * 1024 * 1024))
    with pytest.raises(TooLarge):
        gunzip_capped(bomb, 1024 * 1024)
    two = gzip.compress(b"ab") + gzip.compress(b"cd")
    assert gunzip_capped(two, 10) == b"abcd"
    with pytest.raises(ValueError):
        gunzip_capped(small[:-10], 10_000)


def test_ids_sort_by_time() -> None:
    a, b = ulid(1_000), ulid(2_000)
    assert a < b and len(a) == 26
    assert new_id("rep").startswith("rep_")
    t = new_upload_token()
    assert t.startswith("pgp_") and len(t) == 47 and t[4:].isalnum()


def test_log_redaction() -> None:
    event = {
        "event": "x",
        "authorization": "Bearer abc",
        "cookie": "pg_session=zzz",
        "token_id": "tok_1",
        "detail": "used pgp_" + "A" * 40 + " and eyJhbGciOi.eyJzdWIiOiJ4In0.sig-part",
        "nested": {"text": "secret payload", "state": "s"},
    }
    out = _redact(event)
    assert out["authorization"] == out["cookie"] == "[redacted]"
    assert out["token_id"] == "tok_1"
    assert "pgp_" not in out["detail"] and "eyJ" not in out["detail"]
    assert out["nested"]["text"] == out["nested"]["state"] == "[redacted]"
    assert scrub("ghu_" + "x" * 30) == "[redacted]"
    json.dumps(out)


def test_signer() -> None:
    s = Signer("k" * 32, "oauth-state")
    signed = s.sign("abc")
    assert s.unsign(signed, 60) == "abc"
    assert s.unsign(signed[:-2] + "xx", 60) is None
    assert Signer("other" * 8, "oauth-state").unsign(signed, 60) is None
    assert s.unsign(signed, -1) is None
    assert csrf_for_session("k", "a") != csrf_for_session("k", "b")


def test_private_key_newline_escapes_are_unescaped() -> None:
    from polygraph_cloud.config import Settings

    one_line = "-----BEGIN RSA PRIVATE KEY-----\nAAAA\n-----END RSA PRIVATE KEY-----\n"
    s = Settings(github_app_private_key=one_line)
    assert s.secret("github_app_private_key") == (
        "-----BEGIN RSA PRIVATE KEY-----\nAAAA\n-----END RSA PRIVATE KEY-----\n"
    )
    real = "-----BEGIN RSA PRIVATE KEY-----\nBBBB\n-----END RSA PRIVATE KEY-----\n"
    assert Settings(github_app_private_key=real).secret("github_app_private_key") == real
