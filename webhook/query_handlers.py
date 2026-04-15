"""Bot navigation command formatters.

Each handler returns a plain string (Markdown-safe) for the webhook
layer to send via Telegram. Callback-producing handlers also return an
optional reply_markup dict.

The handlers here do not know about Flask, requests, or Telegram — they
consume webhook.redis_queries and produce text. app.py wires them to
the chat.
"""
from datetime import datetime, timezone
from typing import Optional
from execution.curation.telegram_poster import _escape_md
import redis_queries


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


def format_help() -> str:
    """Return the /help text (static)."""
    return _HELP_TEXT


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


def format_stats(date_iso: str) -> str:
    """Return /stats text for the given ISO date."""
    stats = redis_queries.stats_for_date(date_iso)
    short = _format_short_date(date_iso) or date_iso
    lines = [
        f"*HOJE · {short}*",
        "",
        f"Scraped     {stats['scraped']}",
        f"Staging     {stats['staging']}",
        f"Arquivados  {stats['archived']}",
        f"Recusados   {stats['rejected']}",
        f"Pipeline    {stats['pipeline']}",
    ]
    return "\n".join(lines)


def _format_hhmm(epoch_seconds: float) -> str:
    """Epoch seconds -> 'HH:MM' UTC."""
    try:
        return datetime.fromtimestamp(float(epoch_seconds), tz=timezone.utc).strftime("%H:%M")
    except (ValueError, OSError):
        return "??:??"


def format_rejections(limit: int = 10) -> str:
    """Return /rejections text — last N feedback entries."""
    entries = redis_queries.list_feedback(limit=limit)
    if not entries:
        return "*RECUSAS*\n\nNenhuma recusa registrada."
    lines = [f"*RECUSAS · últimas {len(entries)}*", ""]
    for i, entry in enumerate(entries, start=1):
        when = _format_hhmm(entry.get("timestamp") or 0)
        reason = entry.get("reason") or ""
        if reason:
            reason_fmt = f'"{_escape_md(_truncate(reason, 80))}"'
        else:
            reason_fmt = "_(sem razão)_"
        lines.append(f"{i}. {when} · {reason_fmt}")
    return "\n".join(lines)


_QUEUE_PAGE_SIZE = 5


def format_queue_page(page: int = 1) -> tuple[str, Optional[dict]]:
    """Return (text, reply_markup) for /queue at given 1-indexed page.

    reply_markup is None when there are no items. Each item row has a
    single callback button 'queue_open:<id>' that the dispatch layer
    maps to rendering the full curation card. Pagination row appended
    if total pages > 1.
    """
    items = redis_queries.list_staging(limit=200)
    total = len(items)
    if total == 0:
        return "*STAGING*\n\nNenhum item aguardando.", None

    total_pages = (total + _QUEUE_PAGE_SIZE - 1) // _QUEUE_PAGE_SIZE
    page = max(1, min(page, total_pages))
    start = (page - 1) * _QUEUE_PAGE_SIZE
    end = start + _QUEUE_PAGE_SIZE
    page_items = items[start:end]

    lines = [f"*STAGING · {total} items*", ""]
    for i, item in enumerate(page_items, start=start + 1):
        title = _escape_md(_truncate(item.get("title") or ""))
        lines.append(f"{i}. {title}")
    text = "\n".join(lines)

    keyboard: list[list[dict]] = []
    for i, item in enumerate(page_items, start=start + 1):
        item_id = item.get("id") or ""
        keyboard.append([{
            "text": f"{i}. Abrir",
            "callback_data": f"queue_open:{item_id}",
        }])

    if total_pages > 1:
        row: list[dict] = []
        if page > 1:
            row.append({"text": "⬅ anterior", "callback_data": f"queue_page:{page - 1}"})
        row.append({"text": f"{page}/{total_pages}", "callback_data": "noop"})
        if page < total_pages:
            row.append({"text": "próximo ➡", "callback_data": f"queue_page:{page + 1}"})
        keyboard.append(row)

    return text, {"inline_keyboard": keyboard}
