"""Bot navigation command formatters.

Each handler returns a plain string (Markdown-safe) for the webhook
layer to send via Telegram. Callback-producing handlers also return an
optional reply_markup dict.

The handlers here do not know about Flask, requests, or Telegram — they
consume webhook.redis_queries and produce text. app.py wires them to
the chat.
"""
from datetime import datetime, timezone, timedelta

_BRT = timezone(timedelta(hours=-3))
from typing import Optional
from execution.curation.telegram_poster import _escape_md
import redis_queries


_HELP_TEXT = """*COMANDOS*

/queue — items aguardando
/history \\[DD/MM] — banco de notícias por data
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


_HIST_BTN_TITLE_MAX = 40
_HIST_STATUS_ICONS = {"staged": "🗂️", "archived": "📦", "rejected": "🗑️"}
_HIST_FILTERS = [("all", "Todos"), ("news", "🗞️ News"), ("rationale", "📊 Rationale")]


def today_brt_iso() -> str:
    """Return today's date in BRT as YYYY-MM-DD."""
    return datetime.now(_BRT).strftime("%Y-%m-%d")


def _shift_day(date_iso: str, days: int) -> str:
    """'2026-04-14' ± days -> ISO date string."""
    day = datetime.strptime(date_iso, "%Y-%m-%d") + timedelta(days=days)
    return day.strftime("%Y-%m-%d")


def format_history_page(date_iso: str, flt: str = "all") -> tuple:
    """Return (text, reply_markup) for the news bank at the given BRT day.

    Every item scraped that day appears as a button (status icon + type icon
    + title) opening the full card via hist_open. The header rows carry the
    type-filter chips and the footer navigates between days, both preserving
    the active filter.
    """
    type_filter = None if flt == "all" else flt
    items = redis_queries.list_news_by_day(date_iso, type_filter)
    short = _format_short_date(date_iso) or date_iso

    if items:
        noun = "item" if len(items) == 1 else "items"
        text = (
            f"*📚 BANCO · {short} · {len(items)} {noun}*\n"
            f"🗂️ fila · 📦 enviada · 🗑️ recusada"
        )
    else:
        text = f"*📚 BANCO · {short}*\n\nNenhuma notícia neste dia."

    keyboard: list = []
    chips = []
    for value, label in _HIST_FILTERS:
        chip_label = f"✓ {label}" if value == flt else label
        chips.append({"text": chip_label, "callback_data": f"hist_nav:{date_iso}:{value}"})
    keyboard.append(chips)

    for item in items:
        item_id = item.get("id") or ""
        status_icon = _HIST_STATUS_ICONS.get(item.get("status") or "", "❔")
        title = _truncate(item.get("title") or "", _HIST_BTN_TITLE_MAX)
        keyboard.append([{
            "text": f"{status_icon}{_type_icon(item)} {title}",
            "callback_data": f"hist_open:{item_id}",
        }])

    prev_iso = _shift_day(date_iso, -1)
    nav = [{
        "text": f"⬅ {_format_short_date(prev_iso)}",
        "callback_data": f"hist_nav:{prev_iso}:{flt}",
    }]
    if date_iso < today_brt_iso():
        next_iso = _shift_day(date_iso, 1)
        nav.append({
            "text": f"{_format_short_date(next_iso)} ➡",
            "callback_data": f"hist_nav:{next_iso}:{flt}",
        })
    keyboard.append(nav)

    return text, {"inline_keyboard": keyboard}


def format_stats(date_iso: str) -> str:
    """Return /stats text for the given ISO date (polished layout)."""
    stats = redis_queries.stats_for_date(date_iso)
    short = _format_short_date(date_iso) or date_iso
    lines = [
        f"*📊 HOJE · {short}*",
        "────────────────────",
        f"🔎 Scraped        {stats['scraped']}",
        f"🗂️ Staging        {stats['staging']}",
        f"📦 Arquivados     {stats['archived']}",
        f"❌ Recusados       {stats['rejected']}",
        f"🖋️ No Writer       {stats['pipeline']}",
    ]
    return "\n".join(lines)


def _format_hhmm(epoch_seconds: float) -> str:
    """Epoch seconds -> 'HH:MM' BRT."""
    try:
        return datetime.fromtimestamp(float(epoch_seconds), tz=_BRT).strftime("%H:%M")
    except (ValueError, OSError):
        return "??:??"


def format_rejections(limit: int = 10) -> str:
    """Return /rejections text — last N feedback entries with time + reason."""
    entries = redis_queries.list_feedback(limit=limit)
    if not entries:
        return "*💭 RECUSAS*\n\nNenhuma recusa registrada."
    lines = [
        f"*💭 RECUSAS · últimas {len(entries)}*",
        "────────────────────",
    ]
    for i, entry in enumerate(entries, start=1):
        when = _format_hhmm(entry.get("timestamp") or 0)
        reason = entry.get("reason") or ""
        if reason:
            reason_fmt = f'"{_escape_md(_truncate(reason, 80))}"'
        else:
            reason_fmt = "_(sem razão)_"
        lines.append(f"{i}. 🕒 {when} · {reason_fmt}")
    return "\n".join(lines)


_QUEUE_PAGE_SIZE = 5
_QUEUE_BTN_TITLE_MAX = 40
_ICON_BY_TYPE = {"news": "🗞️", "rationale": "📊"}


def _type_icon(item: dict) -> str:
    """Return the type icon for the item (news 🗞️ or rationale 📊, default news)."""
    return _ICON_BY_TYPE.get(item.get("type", "news"), "🗞️")


def _format_staged_time(iso_date: str) -> str:
    """'2026-04-17T12:30:45+00:00' -> '09:30' (BRT). Returns '' on failure."""
    if not iso_date:
        return ""
    try:
        # Python 3.9's fromisoformat rejects the 'Z' UTC suffix; normalize first.
        normalized = iso_date[:-1] + "+00:00" if iso_date.endswith("Z") else iso_date
        dt = datetime.fromisoformat(normalized).astimezone(_BRT)
        return dt.strftime("%H:%M")
    except (ValueError, TypeError):
        return ""


def _queue_button_text(item: dict) -> str:
    """Return the button text with type icon + truncated title."""
    icon = _type_icon(item)
    title = (item.get("title") or "").strip()
    if len(title) > _QUEUE_BTN_TITLE_MAX:
        title = title[:_QUEUE_BTN_TITLE_MAX] + "…"
    return f"{icon} {title}"


def _format_queue_normal(total: int, time_info: str, page_items: list[dict],
                         total_pages: int, page: int) -> tuple[str, dict]:
    text = f"*🗂️ STAGING · {total} items{time_info}*"
    keyboard: list[list[dict]] = []
    keyboard.append([{
        "text": "☑️ Modo seleção",
        "callback_data": "q_mode:enter",
    }])
    for item in page_items:
        item_id = item.get("id") or ""
        staged = _format_staged_time(item.get("stagedAt", ""))
        time_tag = f" 🕐{staged}" if staged else ""
        keyboard.append([{
            "text": _queue_button_text(item) + time_tag,
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


def _format_queue_select(total: int, selected: set, time_info: str,
                         page_items: list[dict], total_pages: int,
                         page: int) -> tuple[str, dict]:
    selected_count = len(selected)
    noun = "selecionado" if selected_count == 1 else "selecionados"
    text = f"*🗂️ STAGING · {selected_count} {noun} de {total}{time_info}*"
    keyboard: list[list[dict]] = []
    for item in page_items:
        item_id = item.get("id") or ""
        staged = _format_staged_time(item.get("stagedAt", ""))
        time_tag = f" 🕐{staged}" if staged else ""
        check = "☑️" if item_id in selected else "☐"
        label = f"{check} {_queue_button_text(item)}{time_tag}"
        keyboard.append([{
            "text": label,
            "callback_data": f"q_sel:{item_id}",
        }])
    keyboard.append([
        {"text": "✅ Todos", "callback_data": "q_all"},
        {"text": "❌ Nenhum", "callback_data": "q_none"},
    ])
    keyboard.append([
        {"text": f"📦 Arquivar {selected_count}", "callback_data": "q_bulk:archive"},
        {"text": f"🗑️ Descartar {selected_count}", "callback_data": "q_bulk:discard"},
    ])
    keyboard.append([{"text": "🔙 Sair", "callback_data": "q_mode:exit"}])
    if total_pages > 1:
        row: list[dict] = []
        if page > 1:
            row.append({"text": "⬅ anterior", "callback_data": f"queue_page:{page - 1}"})
        row.append({"text": f"{page}/{total_pages}", "callback_data": "noop"})
        if page < total_pages:
            row.append({"text": "próximo ➡", "callback_data": f"queue_page:{page + 1}"})
        keyboard.append(row)
    return text, {"inline_keyboard": keyboard}


def format_queue_page(
    page: int = 1,
    mode: str = "normal",
    selected: Optional[set] = None,
) -> tuple[str, Optional[dict]]:
    """Return (text, reply_markup) for /queue at given 1-indexed page.

    mode='normal' (default) renders each item as a single button opening
    the curation card, plus an '☑️ Modo seleção' entry button at the top.

    mode='select' renders each item as a checkbox toggle, plus action
    rows (✅ Todos / ❌ Nenhum / 📦 Arquivar N / 🗑️ Descartar N / 🔙 Sair).
    'selected' must be the set of currently selected item ids.
    """
    if selected is None:
        selected = set()
    items = redis_queries.list_staging(limit=200)
    total = len(items)
    if total == 0:
        return "*🗂️ STAGING*\n\nNenhum item aguardando.", None

    total_pages = (total + _QUEUE_PAGE_SIZE - 1) // _QUEUE_PAGE_SIZE
    page = max(1, min(page, total_pages))
    start = (page - 1) * _QUEUE_PAGE_SIZE
    end = start + _QUEUE_PAGE_SIZE
    page_items = items[start:end]

    staged_times = [i.get("stagedAt", "") for i in items if i.get("stagedAt")]
    if staged_times:
        oldest = _format_staged_time(min(staged_times))
        newest = _format_staged_time(max(staged_times))
        time_info = (
            f" · coletados {oldest}–{newest} BRT"
            if oldest != newest
            else f" · coletado {newest} BRT"
        )
    else:
        time_info = ""

    if mode == "select":
        return _format_queue_select(total, selected, time_info, page_items, total_pages, page)
    return _format_queue_normal(total, time_info, page_items, total_pages, page)
