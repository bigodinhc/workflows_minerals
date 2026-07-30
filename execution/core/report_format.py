"""Shared rendering primitives for the cron reports posted to the channel.

The three daily crons (SGX prices, Platts assessments, Baltic freight) render
the same two shapes — a 4-line header and one row per instrument. Keeping them
here means the next aesthetic change is one edit instead of three identical
ones.

Output is WhatsApp marker syntax: it renders in Telegram after
webhook/bot/channel_delivery.to_telegram_html(), and pastes into WhatsApp
unchanged. That double duty is why the markers stay in the source text.
"""

from __future__ import annotations

from datetime import date

# Pill dates: PT-BR, CAPS, 3 letters — mirrors the Curator prompt's date rule
# so cron posts and news posts read the same in the channel.
MONTHS_PT_ABBR = {
    1: "JAN", 2: "FEV", 3: "MAR", 4: "ABR", 5: "MAI", 6: "JUN",
    7: "JUL", 8: "AGO", 9: "SET", 10: "OUT", 11: "NOV", 12: "DEZ",
}

# Contract labels ('FEB/26' -> 'Fev/26'). Only daily_report has contract months.
MONTHS_EN_TO_PT = {
    "JAN": "Jan", "FEB": "Fev", "MAR": "Mar", "APR": "Abr",
    "MAY": "Mai", "JUN": "Jun", "JUL": "Jul", "AUG": "Ago",
    "SEP": "Set", "OCT": "Out", "NOV": "Nov", "DEC": "Dez",
}

DIVIDER = "─────────────────"

MARKER_UP = "↑"
MARKER_DOWN = "↓"
MARKER_FLAT = "·"


def pill_date(when: date) -> str:
    """'30/JUL' — the date half of the header pill."""
    return f"{when.day:02d}/{MONTHS_PT_ABBR[when.month]}"


def build_header(title: str, asset: str, when: date) -> str:
    """The canonical 4-line header, shared with the Curator prompt.

    📊 *MINERALS TRADING*
    *<title>*
    `<ASSET> · <DD/MMM>`
    ─────────────────
    """
    return "\n".join([
        "📊 *MINERALS TRADING*",
        f"*{title}*",
        f"`{asset} · {pill_date(when)}`",
        DIVIDER,
    ])


def translate_contract_month(month_code: str) -> str:
    """'FEB/26' or 'FEB26' -> 'Fev/26'. Unknown months pass through as-is."""
    code = month_code.upper().replace("/", "")
    return f"{MONTHS_EN_TO_PT.get(code[:3], code[:3])}/{code[3:]}"


def marker_for(change: float) -> str:
    """↑ rising, ↓ falling, · flat."""
    if change > 0:
        return MARKER_UP
    if change < 0:
        return MARKER_DOWN
    return MARKER_FLAT


def format_row(name: str, value: str, stats: str, marker: str) -> str:
    """One instrument per line: plain name, mono value, plain stats, marker.

    No '> ' prefix on purpose. A blockquote tints the whole line grey, which
    drowned the mono value — the one thing on the line meant to stand out.
    """
    return f"{name}  `{value}`  {stats} {marker}"
