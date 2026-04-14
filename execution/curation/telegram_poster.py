"""Format and send Telegram curation messages.

Each Platts item becomes one Telegram message with:
- Preview (markdown, ~400 chars)
- Inline keyboard: [📖 Ler completo] [✅ Arquivar] [❌ Recusar] [🤖 3 Agents]
"""
from execution.integrations.telegram_client import TelegramClient

_PREVIEW_CHAR_LIMIT = 400


def build_preview(text: str, limit: int = _PREVIEW_CHAR_LIMIT) -> str:
    """Truncate text to limit chars, append '...' if truncated."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _is_flash(item: dict) -> bool:
    return (item.get("source") or "").startswith("allInsights.flash")


def format_message(item: dict) -> str:
    """Render a Telegram Markdown message for an item."""
    title = item.get("title", "")
    full_text = item.get("fullText", "")
    publish_date = item.get("publishDate") or item.get("date") or ""
    source = item.get("source", "")
    author = item.get("author", "")
    tab_name = item.get("tabName", "")
    item_id = item.get("id", "")

    preview = build_preview(full_text)

    if _is_flash(item):
        header = f"*🔴 FLASH* {publish_date}\n━━━━━━━━━━━━━━━━━━━━\n"
        title_line = f"*{title}*\n\n" if title and title != full_text else ""
        footer_meta = "━━━━━━━━━━━━━━━━━━━━"
    else:
        header = ""
        title_line = f"*{title}*\n\n"
        meta_lines = [f"📰 {source}"]
        if tab_name:
            meta_lines.append(f"🔖 {tab_name}")
        if author:
            meta_lines.append(f"✍️ {author}")
        if publish_date:
            meta_lines.append(f"📅 {publish_date}")
        meta_lines.append(f"🆔 `{item_id}`")
        footer_meta = "━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(meta_lines)

    return f"{header}{title_line}{preview}\n\n{footer_meta}"


def build_keyboard(item_id: str, preview_url: str) -> dict:
    """Build Telegram inline keyboard: URL button + 3 callback buttons."""
    return {
        "inline_keyboard": [
            [{"text": "📖 Ler completo", "url": preview_url}],
            [
                {"text": "✅ Arquivar", "callback_data": f"curate_archive:{item_id}"},
                {"text": "❌ Recusar", "callback_data": f"curate_reject:{item_id}"},
                {"text": "🤖 3 Agents", "callback_data": f"curate_pipeline:{item_id}"},
            ],
        ]
    }


def _send_message(chat_id: int, text: str, reply_markup: dict, parse_mode: str = "Markdown") -> None:
    """Send via TelegramClient. Separated for easy mocking in tests."""
    client = TelegramClient()
    client.send_message(text=text, chat_id=chat_id, reply_markup=reply_markup, parse_mode=parse_mode)


def post_for_curation(chat_id: int, item: dict, preview_base_url: str) -> None:
    """Send one curation message for item."""
    text = format_message(item)
    preview_url = f"{preview_base_url.rstrip('/')}/preview/{item['id']}"
    keyboard = build_keyboard(item["id"], preview_url)
    _send_message(chat_id, text, keyboard)
