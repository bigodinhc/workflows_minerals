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
TELEGRAM_TEXT_LIMIT = 4096
# Telegram counts HTML tags against the cap and converting adds ~21% on real
# messages, so the raw input is truncated with headroom first: 3300 × 1.21
# still clears 4096. At the previous 3500 a full-length post converted to
# ~4230 and Telegram would have rejected it.
RAW_TEXT_LIMIT = 3300
TELEGRAM_CAPTION_LIMIT = 1024
COPY_LABEL = "📋 Copiar pro WhatsApp:"

# WhatsApp-style markers produced by the Curator prompt. Paired, same-line
# (except ``` blocks), no whitespace hugging the marker — unbalanced or
# intra-word markers fall through and render literally.
_PRE_RE = re.compile(r"```(.+?)```", re.DOTALL)
_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*(\S(?:[^*\n]*\S)?)\*")
_ITALIC_RE = re.compile(r"(?<![\w&])_(\S(?:[^_\n]*\S)?)_(?![\w;])")
# WhatsApp quote marker ('> line'); '>' has already been HTML-escaped to
# '&gt;' by escape_html by the time this pass runs. Consecutive quote lines
# collapse into a single <blockquote>; separate groups become separate ones.
_QUOTE_BLOCK_RE = re.compile(r"(?m)^(?:&gt; ?.*(?:\n|$))+")


def escape_html(text: str) -> str:
    """Escape &, <, > for parse_mode=HTML. Quotes stay readable."""
    return html.escape(text, quote=False)


def _quote_lines_to_blockquote(text: str) -> str:
    """Group consecutive '&gt; ' lines into a single <blockquote>.

    Separate groups (split by non-quote lines) become separate blockquotes.
    Runs after the marker conversions so *bold*/`code`/_italic_ inside a
    quote line are already HTML by the time the prefix is stripped.
    """
    def _repl(match: re.Match) -> str:
        block = match.group(0)
        trailing_nl = "\n" if block.endswith("\n") else ""
        lines = [re.sub(r"^&gt; ?", "", ln) for ln in block.rstrip("\n").split("\n")]
        return "<blockquote>" + "\n".join(lines) + "</blockquote>" + trailing_nl
    return _QUOTE_BLOCK_RE.sub(_repl, text)


def to_telegram_html(text: str) -> str:
    """Escape HTML, then convert WhatsApp markers to Telegram HTML tags.

    ```x``` → <pre>x</pre>, `x` → <code>x</code>, *x* → <b>x</b>,
    _x_ → <i>x</i>. Conversion is deterministic and per-pair: a stray
    marker stays literal instead of breaking the whole post. Finally,
    consecutive '> ' quote lines (WhatsApp marker, already emitted by the
    Curator prompt) collapse into a <blockquote> panel.
    """
    escaped = escape_html(text)
    with_pre = _PRE_RE.sub(r"<pre>\1</pre>", escaped)
    with_code = _CODE_RE.sub(r"<code>\1</code>", with_pre)
    with_bold = _BOLD_RE.sub(r"<b>\1</b>", with_code)
    converted = _ITALIC_RE.sub(r"<i>\1</i>", with_bold)
    return _quote_lines_to_blockquote(converted)


def has_whatsapp_markers(text: str) -> bool:
    """True when the source carries markup worth preserving for a paste.

    Keeps the copy block off posts that gain nothing from it — the
    platts_reports post is a bare '📄 filename' next to a PDF attachment.
    """
    if not text:
        return False
    if _BOLD_RE.search(text) or _CODE_RE.search(text) or _ITALIC_RE.search(text):
        return True
    return text.lstrip().startswith("> ") or "\n> " in text


def build_copy_block(raw: str) -> str:
    """Label plus a <pre> block holding the raw source, HTML-escaped.

    Telegram renders <pre> with a copy affordance, and what lands on the
    clipboard is the literal marker syntax — which is what WhatsApp parses.
    Copying the rendered post instead would paste as flat text.
    """
    return f"{COPY_LABEL}\n<pre>{escape_html(raw)}</pre>"


def build_channel_payload(raw: str) -> list[str]:
    """Messages to post, in order.

    One element when the readable post and its copy block fit a single
    message; two when they don't. Content is never truncated — a long report
    costs an extra message rather than losing rows.
    """
    pretty = to_telegram_html(raw)
    if not has_whatsapp_markers(raw):
        return [pretty]

    copy_block = build_copy_block(raw)
    if len(copy_block) > TELEGRAM_TEXT_LIMIT:
        # Escaping blew the cap on its own — ship the readable post rather
        # than a block Telegram would reject outright.
        return [pretty]

    combined = f"{pretty}\n\n{copy_block}"
    if len(combined) <= TELEGRAM_TEXT_LIMIT:
        return [combined]
    return [pretty, copy_block]


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

    try:
        bot = get_bot()
        text = to_telegram_html(message[:RAW_TEXT_LIMIT])
    except Exception as exc:
        logger.error(f"post_report_to_channel setup failed: {exc}")
        return {"ok": False, "message_id": None, "error": str(exc)[:300]}

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
                parse_mode="HTML",
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
