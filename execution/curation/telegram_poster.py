"""Format and send Telegram curation messages.

Each Platts item becomes one Telegram message with:
- Preview (markdown, ~400 chars)
- Inline keyboard (2×2): [📖 Ler completo] [✅ Arquivar] / [❌ Recusar] [🖋️ Writer]
"""
from execution.integrations.telegram_client import TelegramClient

_PREVIEW_CHAR_LIMIT = 400

_MD_SPECIAL_CHARS = ("\\", "_", "*", "`", "[", "]")


def _escape_md(text: str) -> str:
    """Escape Telegram legacy-Markdown special chars in a dynamic field.

    Order matters: escape backslashes first so subsequent replacements
    don't double-escape.
    """
    if not text:
        return ""
    for ch in _MD_SPECIAL_CHARS:
        text = text.replace(ch, "\\" + ch)
    return text


def build_preview(text: str, limit: int = _PREVIEW_CHAR_LIMIT) -> str:
    """Truncate text to limit chars, append '...' if truncated."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _is_flash(item: dict) -> bool:
    return (item.get("source") or "").startswith("allInsights.flash")


_ICON_BY_TYPE = {"news": "🗞️", "rationale": "📊"}


def _type_icon(item: dict) -> str:
    """Return type icon: 🗞️ for news (default), 📊 for rationale."""
    return _ICON_BY_TYPE.get(item.get("type", "news"), "🗞️")


def format_message(item: dict) -> str:
    """Render a Telegram Markdown message for an item.

    Non-flash layout (v1.1 polished):
        *<icon> Title*

        <preview>

        📅 <date> · 📰 <source> · 🔖 <tab>
        🆔 `<id>`

    Flash layout (preserved):
        *🔴 FLASH* <date>
        ━━━━━━━━━━━━━━━━━━━━
        *Title*

        <preview>

        ━━━━━━━━━━━━━━━━━━━━
    """
    title = item.get("title", "")
    full_text = item.get("fullText", "")
    publish_date = item.get("publishDate") or item.get("date") or ""
    source = item.get("source", "")
    author = item.get("author", "")
    tab_name = item.get("tabName", "")
    item_id = item.get("id", "")

    preview = _escape_md(build_preview(full_text))

    if _is_flash(item):
        header = f"*🔴 FLASH* {_escape_md(publish_date)}\n━━━━━━━━━━━━━━━━━━━━\n"
        title_line = f"*{_escape_md(title)}*\n\n" if title and title != full_text else ""
        footer_meta = "━━━━━━━━━━━━━━━━━━━━"
        return f"{header}{title_line}{preview}\n\n{footer_meta}"

    # Non-flash: compact layout with type icon
    icon = _type_icon(item)
    title_line = f"*{icon} {_escape_md(title)}*" if title else f"*{icon} (sem título)*"

    meta_parts = []
    if publish_date:
        meta_parts.append(f"📅 {_escape_md(publish_date)}")
    if source:
        meta_parts.append(f"📰 {_escape_md(source)}")
    if tab_name:
        meta_parts.append(f"🔖 {_escape_md(tab_name)}")
    if author:
        meta_parts.append(f"✍️ {_escape_md(author)}")
    meta_line = " · ".join(meta_parts) if meta_parts else ""

    body_lines = [title_line, "", preview]
    if meta_line:
        body_lines.append("")
        body_lines.append(meta_line)
    body_lines.append(f"🆔 `{_escape_md(item_id)}`")
    return "\n".join(body_lines)


def build_keyboard(item_id: str, preview_url: str) -> dict:
    """Build Telegram inline keyboard: 2×2 layout with URL + 3 callback buttons.

    Callback format: curate:{action}:{item_id} — matches CurateAction(prefix="curate")
    in webhook/bot/callback_data.py.
    """
    return {
        "inline_keyboard": [
            [
                {"text": "📖 Ler completo", "url": preview_url},
                {"text": "✅ Arquivar", "callback_data": f"curate:archive:{item_id}"},
            ],
            [
                {"text": "❌ Recusar", "callback_data": f"curate:reject:{item_id}"},
                {"text": "🖋️ Writer", "callback_data": f"curate:pipeline:{item_id}"},
            ],
        ]
    }


def _send_message(chat_id: int, text: str, reply_markup: dict, parse_mode: str = "Markdown") -> None:
    """Send via TelegramClient. Separated for easy mocking in tests."""
    client = TelegramClient()
    client.send_message(text=text, chat_id=chat_id, reply_markup=reply_markup, parse_mode=parse_mode)


def post_for_curation(chat_id: int, item: dict, preview_base_url: str) -> None:
    """Send one curation message for item."""
    if not preview_base_url or not preview_base_url.startswith(("http://", "https://")):
        raise ValueError("preview_base_url must be an absolute http(s) URL")
    item_id = item.get("id")
    if not item_id:
        raise ValueError("post_for_curation requires item['id']")
    text = format_message(item)
    preview_url = f"{preview_base_url.rstrip('/')}/preview/{item_id}"
    keyboard = build_keyboard(item_id, preview_url)
    _send_message(chat_id, text, keyboard)
