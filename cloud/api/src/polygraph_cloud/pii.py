"""PII scanning for uploads that include payload text.

Only payload texts are scanned (``Payload.text`` in final answers and ledger
entries). A finding names the *kind* of data and where it was found, never the
value itself.

Backends:

``regex`` (always available). Detects:

- **email** addresses, except reserved test/documentation domains from RFC 2606
  and RFC 6761: the TLDs ``.test``, ``.example``, ``.invalid``, ``.localhost`` and
  the domains ``example.com``, ``example.net``, ``example.org`` (and their
  subdomains). Those can never belong to a real person, so test data can use them.
- **phone** numbers: Indian mobile numbers (optional ``+91``/``0091``/``0``
  prefix, 10 digits starting 6-9, optionally split 5+5) and E.164 numbers
  (``+`` country code and 8-15 digits, light separators allowed).
- **pan**: Indian PAN, ``[A-Z]{5}[0-9]{4}[A-Z]``.
- **aadhaar**: 12 digits (first digit 2-9, optionally grouped 4-4-4) that pass the
  **Verhoeff** checksum. Random 12-digit numbers mostly fail it, which keeps
  false alarms low.
- **ip_address**: IPv4 and IPv6 addresses that are *globally routable*. Private
  (RFC 1918, ``fc00::/7``), loopback, link-local, CGNAT (100.64/10), multicast,
  reserved, unspecified and documentation ranges (192.0.2/24, 198.51.100/24,
  203.0.113/24, 2001:db8::/32) are allowed: they cannot point at a person on the
  internet, and test fixtures use them constantly.

``presidio`` (when ``presidio-analyzer`` is installed). Runs Microsoft Presidio
with email, phone, IP, credit card, Indian PAN and Aadhaar recognisers, then
applies the same reserved-domain and private-IP exceptions, *plus* the regex
backend (so it is never weaker than ``regex``).
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

from polygraph.results import ResultsFile

KIND_LABELS = {
    "email": "an email address",
    "phone": "a phone number",
    "pan": "a PAN number",
    "aadhaar": "an Aadhaar number",
    "ip_address": "a public IP address",
    "credit_card": "a payment card number",
}


@dataclass(frozen=True)
class Finding:
    kind: str
    run_id: str
    field: str

    def message(self) -> str:
        label = KIND_LABELS.get(self.kind, "personal data")
        return (
            f"Upload rejected: payload text in run {self.run_id} ({self.field}) looks like it "
            f"contains {label}. Mask it in your polygraph mask function or turn off text "
            f"upload, then upload again."
        )


class Scanner(Protocol):
    name: str

    def kinds(self, text: str) -> list[str]: ...


# --------------------------------------------------------------------------- #
# Verhoeff checksum (used by Aadhaar)
# --------------------------------------------------------------------------- #

_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]
_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def verhoeff_valid(digits: str) -> bool:
    if not digits.isdigit():
        return False
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def verhoeff_check_digit(digits: str) -> str:
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _D[c][_P[(i + 1) % 8][int(ch)]]
    return str(_INV[c])


# --------------------------------------------------------------------------- #
# Regex backend
# --------------------------------------------------------------------------- #

_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@((?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63})(?![A-Za-z0-9-])"
)
_RESERVED_TLDS = {"test", "example", "invalid", "localhost"}
_RESERVED_DOMAINS = {"example.com", "example.net", "example.org"}

_IN_MOBILE = re.compile(r"(?<![\d+])(?:(?:\+|00)91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")
_E164 = re.compile(r"(?<![\w+])\+[1-9](?:[\s.-]?\d){7,14}(?![\d])")
_PAN = re.compile(r"(?<![A-Za-z0-9])[A-Z]{5}[0-9]{4}[A-Z](?![A-Za-z0-9])")
_AADHAAR = re.compile(r"(?<![\d])([2-9]\d{3})([\s-]?)(\d{4})\2(\d{4})(?![\d])")
_IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
_IPV6 = re.compile(
    r"(?<![\w:.])(?:[0-9A-Fa-f]{0,4}:){2,7}(?:[0-9A-Fa-f]{0,4}|(?:\d{1,3}\.){3}\d{1,3})(?![\w:])"
)

_DOC_NETS = [
    ipaddress.ip_network(n)
    for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "2001:db8::/32", "3fff::/20")
]


def is_reserved_email_domain(domain: str) -> bool:
    d = domain.lower().rstrip(".")
    tld = d.rsplit(".", 1)[-1]
    if tld in _RESERVED_TLDS:
        return True
    return any(d == r or d.endswith("." + r) for r in _RESERVED_DOMAINS)


def is_personal_ip(text: str) -> bool:
    """True only for globally routable, non-documentation addresses."""
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False
    if any(ip in net for net in _DOC_NETS if net.version == ip.version):
        return False
    return bool(ip.is_global) and not ip.is_multicast


def _emails(text: str) -> Iterator[str]:
    for m in _EMAIL.finditer(text):
        if not is_reserved_email_domain(m.group(1)):
            yield "email"


class RegexScanner:
    name = "regex"

    def kinds(self, text: str) -> list[str]:
        found: list[str] = []
        if "@" in text and any(True for _ in _emails(text)):
            found.append("email")
        if _IN_MOBILE.search(text) or _E164.search(text):
            found.append("phone")
        if _PAN.search(text):
            found.append("pan")
        for m in _AADHAAR.finditer(text):
            if verhoeff_valid(m.group(1) + m.group(3) + m.group(4)):
                found.append("aadhaar")
                break
        for m in _IPV4.finditer(text):
            if is_personal_ip(m.group(0)):
                found.append("ip_address")
                break
        else:
            if ":" in text:
                for m in _IPV6.finditer(text):
                    if is_personal_ip(m.group(0)):
                        found.append("ip_address")
                        break
        return found


# --------------------------------------------------------------------------- #
# Presidio backend
# --------------------------------------------------------------------------- #


def presidio_available() -> bool:
    try:
        import presidio_analyzer  # noqa: F401
    except Exception:
        return False
    return True


_PRESIDIO_KINDS = {
    "EMAIL_ADDRESS": "email",
    "PHONE_NUMBER": "phone",
    "IP_ADDRESS": "ip_address",
    "CREDIT_CARD": "credit_card",
    "IN_PAN": "pan",
    "IN_AADHAAR": "aadhaar",
}


class PresidioScanner:
    name = "presidio"

    def __init__(self) -> None:
        import os

        from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
        from presidio_analyzer.nlp_engine import NlpEngineProvider

        # Every entity we look for is pattern-based, so a small spaCy model is enough;
        # Presidio's default (en_core_web_lg) adds hundreds of MB for nothing here.
        model = os.environ.get("PRESIDIO_SPACY_MODEL", "en_core_web_sm")
        nlp = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "en", "model_name": model}],
            }
        ).create_engine()
        self._engine = AnalyzerEngine(nlp_engine=nlp, supported_languages=["en"])
        registry = self._engine.registry
        have = {e for r in registry.recognizers for e in r.supported_entities}
        if "IN_PAN" not in have:
            registry.add_recognizer(
                PatternRecognizer(
                    supported_entity="IN_PAN",
                    patterns=[Pattern("pan", r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", 0.6)],
                )
            )
        if "IN_AADHAAR" not in have:

            class _Aadhaar(PatternRecognizer):  # type: ignore[misc]
                def validate_result(self, pattern_text: str) -> bool:
                    return verhoeff_valid(re.sub(r"\D", "", pattern_text))

            registry.add_recognizer(
                _Aadhaar(
                    supported_entity="IN_AADHAAR",
                    patterns=[Pattern("aadhaar", r"\b[2-9]\d{3}[\s-]?\d{4}[\s-]?\d{4}\b", 0.5)],
                )
            )
        self._regex = RegexScanner()

    def kinds(self, text: str) -> list[str]:
        found = set(self._regex.kinds(text))
        results = self._engine.analyze(
            text=text, language="en", entities=list(_PRESIDIO_KINDS), score_threshold=0.5
        )
        for r in results:
            kind = _PRESIDIO_KINDS.get(r.entity_type)
            if kind is None:
                continue
            value = text[r.start : r.end]
            if kind == "email" and is_reserved_email_domain(value.rsplit("@", 1)[-1]):
                continue
            if kind == "ip_address" and not is_personal_ip(value):
                continue
            found.add(kind)
        return sorted(found)


# --------------------------------------------------------------------------- #


@lru_cache(maxsize=4)
def get_scanner(name: str = "regex") -> Scanner:
    if name == "presidio" and presidio_available():
        return PresidioScanner()
    return RegexScanner()


def iter_payload_texts(results: ResultsFile) -> Iterator[tuple[str, str, str]]:
    """Yield ``(run_id, field, text)`` for every payload text in the file."""
    for run in results.runs:
        if run.final_answer is not None and run.final_answer.text:
            yield run.id, "final_answer", run.final_answer.text
        for i, e in enumerate(run.ledger):
            if e.asked.text:
                yield run.id, f"ledger[{i}].asked", e.asked.text
            if e.told is not None and e.told.text:
                yield run.id, f"ledger[{i}].told", e.told.text
            if e.happened.server_result is not None and e.happened.server_result.text:
                yield run.id, f"ledger[{i}].happened.server_result", e.happened.server_result.text


def scan_results(results: ResultsFile, scanner: Scanner) -> Finding | None:
    """Return the first finding, or None when every payload text is clean."""
    for run_id, field, text in iter_payload_texts(results):
        kinds = scanner.kinds(text)
        if kinds:
            return Finding(kind=kinds[0], run_id=run_id, field=field)
    return None
