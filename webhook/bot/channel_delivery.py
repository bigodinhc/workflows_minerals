"""Posting client reports to the private Telegram channel.

One post reaches every channel subscriber — no per-user loop, no ban
risk. Mirrors the never-raise posture of execution/core/event_bus.py
sinks: failures come back as a status dict, never as an exception.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re

from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import BufferedInputFile

from bot.config import get_bot, TELEGRAM_CLIENT_CHANNEL_ID

logger = logging.getLogger(__name__)

MAX_FLOOD_RETRIES = 3
# Telegram caps message text at 4096 chars counting HTML tags; converting
# adds tag overhead, so we truncate the raw input with headroom first.
RAW_TEXT_LIMIT = 3500
TELEGRAM_CAPTION_LIMIT = 1024

# WhatsApp-style markers produced by the Curator prompt. Paired, same-line
# (except ``` blocks), no whitespace hugging the marker — unbalanced or
# intra-word markers fall through and render literally.
_PRE_RE = re.compile(r"```(.+?)```", re.DOTALL)
_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*(\S(?:[^*\n]*\S)?)\*")
_ITALIC_RE = re.compile(r"(?<![\w&])_(\S(?:[^_\n]*\S)?)_(?![\w;])")


def escape_html(text: str) -> str:
    """Escape &, <, > for parse_mode=HTML. Quotes stay readable."""
    return html.escape(text, quote=False)


def to_telegram_html(text: str) -> str:
    """Escape HTML, then convert WhatsApp markers to Telegram HTML tags.

    ```x``` → <pre>x</pre>, `x` → <code>x</code>, *x* → <b>x</b>,
    _x_ → <i>x</i>. Conversion is deterministic and per-pair: a stray
    marker stays literal instead of breaking the whole post.
    """
    escaped = escape_html(text)
    with_pre = _PRE_RE.sub(r"<pre>\1</pre>", escaped)
    with_code = _CODE_RE.sub(r"<code>\1</code>", with_pre)
    with_bold = _BOLD_RE.sub(r"<b>\1</b>", with_code)
    return _ITALIC_RE.sub(r"<i>\1</i>", with_bold)


async def _call_with_flood_retry(coro_factory):
    """Await coro_factory(); on TelegramRetryAfter sleep retry_after and retry
    (up to MAX_FLOOD_RETRIES attempts total). Re-raises the last error."""
    for attempt in range(MAX_FLOOD_RETRIES):
        try:
            return await coro_factory()
        except TelegramRetryAfter as exc:
            if attempt == MAX_FLOOD_RETRIES - 1:
                raise
            logger.warning(f"channel flood-wait: sleeping {exc.retry_after}s")
            await asyncio.sleep(exc.retry_after)


async def post_report_to_channel(
    message: str,
    pdf: bytes | None = None,
    pdf_filename: str = "report.pdf",
    *,
    silent: bool = False,
    pin: bool = False,
) -> dict:
    """Post a client report to TELEGRAM_CLIENT_CHANNEL_ID. Never raises.

    Returns {"ok": bool, "message_id": int | None, "error": str | None}.
    A PDF send failure after a successful text post keeps ok=True and
    records the problem in "error" (spec §5: PDF must not block the summary).
    """
    if not TELEGRAM_CLIENT_CHANNEL_ID:
        logger.error("TELEGRAM_CLIENT_CHANNEL_ID not set — channel post skipped")
        return {
            "ok": False,
            "message_id": None,
            "error": "TELEGRAM_CLIENT_CHANNEL_ID not set",
        }

    bot = get_bot()
    text = to_telegram_html(message[:RAW_TEXT_LIMIT])

    try:
        sent = await _call_with_flood_retry(lambda: bot.send_message(
            TELEGRAM_CLIENT_CHANNEL_ID,
            text,
            parse_mode="HTML",
            disable_notification=silent,
        ))
    except Exception as exc:
        logger.error(f"post_report_to_channel send_message failed: {exc}")
        return {"ok": False, "message_id": None, "error": str(exc)[:300]}

    result = {"ok": True, "message_id": sent.message_id, "error": None}

    if pdf is not None:
        try:
            doc = BufferedInputFile(pdf, filename=pdf_filename)
            await _call_with_flood_retry(lambda: bot.send_document(
                TELEGRAM_CLIENT_CHANNEL_ID,
                doc,
                caption=escape_html(pdf_filename)[:TELEGRAM_CAPTION_LIMIT],
                disable_notification=True,
            ))
        except Exception as exc:
            logger.error(f"post_report_to_channel send_document failed: {exc}")
            result = {**result, "error": f"pdf_send_failed: {str(exc)[:200]}"}

    if pin:
        try:
            await _call_with_flood_retry(lambda: bot.pin_chat_message(
                TELEGRAM_CLIENT_CHANNEL_ID,
                sent.message_id,
                disable_notification=True,
            ))
        except Exception as exc:
            logger.warning(f"pin_chat_message failed: {exc}")

    return result
