"""Formatter for the post-scrap ingestion digest.

Called by the Platts ingestion script after staging is complete. Returns
None if zero new items (caller sends nothing). Otherwise returns
(text, reply_markup) ready for send_telegram_message.
"""
from typing import Optional, Tuple

from execution.curation.telegram_poster import _escape_md

_PREVIEW_LIMIT = 3
_TITLE_TRUNCATE = 60
_ICON_BY_TYPE = {"news": "🗞️", "rationale": "📊"}


def _truncate(text: str, limit: int = _TITLE_TRUNCATE) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def format_ingestion_digest(
    counters: dict,
    staged_items: list,
) -> Optional[Tuple[str, dict]]:
    """Build the digest (text, markup) or None if 0 staged.

    counters: dict with keys staged, news_staged, rationale_staged.
    staged_items: list of dicts with keys title, type (already tagged
                  by router).
    """
    total = counters.get("staged", 0)
    if total == 0:
        return None

    news = counters.get("news_staged", 0)
    rationale = counters.get("rationale_staged", 0)

    lines = [f"📥 *Ingestão · {total} novas*"]
    # Tree showing non-zero branches only
    if news and rationale:
        lines.append(f"├ 🗞️ {news} notícias")
        lines.append(f"└ 📊 {rationale} rationale")
    elif news:
        lines.append(f"└ 🗞️ {news} notícias")
    elif rationale:
        lines.append(f"└ 📊 {rationale} rationale")

    # Preview: first _PREVIEW_LIMIT items
    preview = staged_items[:_PREVIEW_LIMIT]
    if preview:
        lines.append("")
        for item in preview:
            icon = _ICON_BY_TYPE.get(item.get("type", "news"), "🗞️")
            title = _escape_md(_truncate(item.get("title") or ""))
            lines.append(f"• {icon} {title}")
        remaining = total - len(preview)
        if remaining > 0:
            lines.append(f"+{remaining} mais")

    text = "\n".join(lines)
    markup = {
        "inline_keyboard": [[
            {"text": "🔍 Abrir fila", "callback_data": "queue_page:1"},
        ]],
    }
    return text, markup
