"""A small flat SVG badge: ``polygraph · silent-lie 0% · coverage 18/22``.

The badge is self-contained (no fonts, images or links to fetch). Text widths
come from a fixed per-character table, so the same results always give the
same bytes on every machine.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from ..results import Rate, ResultsFile

INK = "#18212B"
PEN_GREEN = "#2E7A5A"
CRIMSON = "#B3262E"
INK_BLUE = "#2450A6"

_HEIGHT = 20
_PAD = 6  # horizontal padding on each side of a segment's text
_FONT = "Verdana,DejaVu Sans,Geneva,sans-serif"

# Rough advance widths (px) for 11 px Verdana. Close enough for layout.
_NARROW = dict.fromkeys("ijlI.,:;'!|", 3.4)
_SMALL = dict.fromkeys("frt()[]/ -", 4.6)
_WIDE = dict.fromkeys("mwMW%", 10.4)
_DIGITS = dict.fromkeys("0123456789", 7.0)
_DEFAULT_LOWER = 6.6
_DEFAULT_UPPER = 7.6
_DEFAULT_OTHER = 7.0


def _char_width(ch: str) -> float:
    for table in (_NARROW, _SMALL, _WIDE, _DIGITS):
        if ch in table:
            return table[ch]
    if ch.islower():
        return _DEFAULT_LOWER
    if ch.isupper():
        return _DEFAULT_UPPER
    return _DEFAULT_OTHER


def text_width(text: str) -> int:
    """Estimated rendered width of ``text`` in pixels (rounded up)."""
    total = sum(_char_width(ch) for ch in text)
    return int(total + 0.999)


def _pct(rate: Rate) -> str:
    if rate.value is None:
        return "n/a"
    pct = rate.value * 100
    if 0 < pct < 1:
        return "<1%"
    if 99 < pct < 100:
        return ">99%"
    return f"{int(pct + 0.5)}%"  # round half up: 12.5% shows as 13%


def _segments(results: ResultsFile) -> list[tuple[str, str]]:
    rate = results.summary.silent_lie
    lie_colour = CRIMSON if rate.numerator > 0 else PEN_GREEN
    cov = results.coverage
    cov_text = f"{cov.covered}/{cov.applicable}" if cov.applicable else "n/a"
    return [
        ("polygraph", INK),
        (f"silent-lie {_pct(rate)}", lie_colour),
        (f"coverage {cov_text}", INK_BLUE),
    ]


def badge_text(results: ResultsFile) -> str:
    """The badge's words as plain text, e.g. ``polygraph · silent-lie 0% · coverage 18/22``."""
    return " · ".join(text for text, _ in _segments(results))


def render_badge(results: ResultsFile) -> str:
    """Render the badge as a standalone SVG string."""
    segments = _segments(results)
    label = escape(badge_text(results), {'"': "&quot;"})
    widths = [text_width(text) + 2 * _PAD for text, _ in segments]
    total = sum(widths)

    rects: list[str] = []
    texts: list[str] = []
    x = 0
    for (text, colour), width in zip(segments, widths, strict=True):
        rects.append(f'<rect x="{x}" width="{width}" height="{_HEIGHT}" fill="{colour}"/>')
        centre = x + width / 2
        safe = escape(text)
        texts.append(
            f'<text x="{centre:.1f}" y="15" fill="#010101" fill-opacity=".3">{safe}</text>'
            f'<text x="{centre:.1f}" y="14">{safe}</text>'
        )
        x += width

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="{_HEIGHT}" '
        f'viewBox="0 0 {total} {_HEIGHT}" role="img" aria-label="{label}">'
        f"<title>{label}</title>"
        f'<clipPath id="pg-round"><rect width="{total}" height="{_HEIGHT}" rx="3" '
        f'fill="#fff"/></clipPath>'
        f'<g clip-path="url(#pg-round)">{"".join(rects)}</g>'
        f'<g fill="#fff" text-anchor="middle" font-family="{_FONT}" font-size="11">'
        f"{''.join(texts)}</g>"
        "</svg>\n"
    )
