"""The live README badge: ``polygraph | silent-lie 0% · coverage 18/22``.

Colours (deliberately not brand orange, navy or purple, so the badge reads as a
status signal):

- pen green ``#2E7A5A``: silent-lie and double-action are both 0
- amber ``#A86E12``: the worse of the two is low (up to 5 %)
- crimson ``#B3262E``: above 5 %
- grey ``#56636F``: unknown (no results, private or unknown project)
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

GREEN = "#2E7A5A"
AMBER = "#A86E12"
CRIMSON = "#B3262E"
GREY = "#56636F"
LABEL_BG = "#3B4650"

LOW_THRESHOLD = 0.05

# Approximate Verdana 11px advance widths (the shields.io convention).
_WIDTHS = {
    **dict.fromkeys("ijl|.:,;'!", 3.5),
    **dict.fromkeys("frtI()[] -/", 4.6),
    **dict.fromkeys("abcdeghknopqsuvxyz0123456789$?", 6.9),
    **dict.fromkeys("mwMW%", 10.5),
    "·": 4.6,
}


def _text_width(text: str) -> float:
    return sum(_WIDTHS.get(ch, 7.5 if ch.isupper() else 6.9) for ch in text)


def format_rate(value: float | None) -> str:
    if value is None:
        return "n/a"
    pct = value * 100
    if pct == 0 or pct >= 10 or pct.is_integer():
        return f"{pct:.0f}%"
    return f"{pct:.1f}%"


@dataclass
class BadgeData:
    silent_lie: float | None
    double_action: float | None
    covered: int
    applicable: int


def colour_for(data: BadgeData | None) -> str:
    if data is None or data.silent_lie is None:
        return GREY
    worst = max(v for v in (data.silent_lie, data.double_action) if v is not None)
    if worst == 0:
        return GREEN
    if worst <= LOW_THRESHOLD:
        return AMBER
    return CRIMSON


def message_for(data: BadgeData | None) -> str:
    if data is None or data.silent_lie is None:
        return "unknown"
    return f"silent-lie {format_rate(data.silent_lie)} · coverage {data.covered}/{data.applicable}"


def render(data: BadgeData | None, label: str = "polygraph") -> str:
    message = message_for(data)
    colour = colour_for(data)
    lw = round(_text_width(label) + 12)
    mw = round(_text_width(message) + 12)
    total = lw + mw
    lab, msg = escape(label), escape(message)
    title = escape(f"{label}: {message}")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="20" role="img" '
        f'aria-label="{title}">'
        f"<title>{title}</title>"
        '<linearGradient id="s" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" '
        'stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient>'
        f'<clipPath id="r"><rect width="{total}" height="20" rx="3" fill="#fff"/></clipPath>'
        f'<g clip-path="url(#r)"><rect width="{lw}" height="20" fill="{LABEL_BG}"/>'
        f'<rect x="{lw}" width="{mw}" height="20" fill="{colour}"/>'
        f'<rect width="{total}" height="20" fill="url(#s)"/></g>'
        '<g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" '
        'font-size="11">'
        f'<text x="{lw / 2:.1f}" y="15" fill="#010101" fill-opacity=".3">{lab}</text>'
        f'<text x="{lw / 2:.1f}" y="14">{lab}</text>'
        f'<text x="{lw + mw / 2:.1f}" y="15" fill="#010101" fill-opacity=".3">{msg}</text>'
        f'<text x="{lw + mw / 2:.1f}" y="14">{msg}</text>'
        "</g></svg>"
    )
