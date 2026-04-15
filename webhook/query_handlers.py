"""Bot navigation command formatters.

Each handler returns a plain string (Markdown-safe) for the webhook
layer to send via Telegram. Callback-producing handlers also return an
optional reply_markup dict.

The handlers here do not know about Flask, requests, or Telegram — they
consume webhook.redis_queries and produce text. app.py wires them to
the chat.
"""
from webhook import redis_queries


_HELP_TEXT = """*COMANDOS*

/queue — items aguardando
/history — arquivo (últimos 10)
/rejections — recusas (últimas 10)
/stats — contadores de hoje
/status — saúde do sistema
/reprocess <id> — re-dispara pipeline
/add, /list — contatos
/cancel — abortar fluxo"""

_MONTHS_PT = [
    "jan", "fev", "mar", "abr", "mai", "jun",
    "jul", "ago", "set", "out", "nov", "dez",
]

_MD_SPECIALS = ("\\", "*", "_", "[", "]", "`")


def format_help() -> str:
    """Return the /help text (static)."""
    return _HELP_TEXT


def _escape_md(text) -> str:
    """Escape Telegram Markdown (legacy) specials so dynamic content does
    not break parse_mode=Markdown sends.

    Order matters: backslash must be first so subsequent replacements do
    not double-escape inserted backslashes.
    """
    if text is None:
        return ""
    s = str(text)
    for ch in _MD_SPECIALS:
        s = s.replace(ch, "\\" + ch)
    return s


def _format_short_date(iso_date: str) -> str:
    """'2026-04-14' -> '14/abr'. Returns '' on parse failure."""
    if not iso_date or len(iso_date) < 10:
        return ""
    try:
        year, month, day = iso_date[:10].split("-")
        month_idx = int(month) - 1
        if not 0 <= month_idx < 12:
            return ""
        return f"{int(day):02d}/{_MONTHS_PT[month_idx]}"
    except (ValueError, IndexError):
        return ""


def _truncate(text: str, limit: int = 60) -> str:
    """Truncate to limit chars with trailing '…'."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def format_history(limit: int = 10) -> str:
    """Return /history text — last N archived items cross-date."""
    items = redis_queries.list_archive_recent(limit=limit)
    if not items:
        return "*ARQUIVADOS*\n\nNenhum item arquivado."
    lines = [f"*ARQUIVADOS · {len(items)} mais recentes*", ""]
    for i, item in enumerate(items, start=1):
        title = _escape_md(_truncate(item.get("title") or ""))
        date = _format_short_date(item.get("archived_date") or "")
        lines.append(f"{i}. {title} — {date}")
    return "\n".join(lines)
