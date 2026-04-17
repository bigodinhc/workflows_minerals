"""Shared helpers used by multiple routers.

Extracted to avoid circular imports between commands.py and callbacks.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone, timedelta

from bot.config import get_bot
from bot.keyboards import build_approval_keyboard

logger = logging.getLogger(__name__)

# ── Persistent drafts store (Redis, 7d TTL) ──

_DRAFT_KEY_PREFIX = "webhook:draft:"
_DRAFT_TTL_SECONDS = 7 * 24 * 60 * 60


def _drafts_client():
    from execution.curation.redis_client import _get_client
    return _get_client()


def drafts_get(draft_id):
    try:
        raw = _drafts_client().get(f"{_DRAFT_KEY_PREFIX}{draft_id}")
        if raw:
            return json.loads(raw)
    except Exception as exc:
        logger.warning(f"drafts_get({draft_id}) failed: {exc}")
    return None


def drafts_set(draft_id, draft):
    try:
        _drafts_client().set(
            f"{_DRAFT_KEY_PREFIX}{draft_id}",
            json.dumps(draft),
            ex=_DRAFT_TTL_SECONDS,
        )
    except Exception as exc:
        logger.error(f"drafts_set({draft_id}) failed: {exc}")


def drafts_contains(draft_id):
    try:
        return bool(_drafts_client().exists(f"{_DRAFT_KEY_PREFIX}{draft_id}"))
    except Exception as exc:
        logger.warning(f"drafts_contains({draft_id}) failed: {exc}")
        return False


def drafts_update(draft_id, **fields):
    draft = drafts_get(draft_id)
    if draft is None:
        return
    draft.update(fields)
    drafts_set(draft_id, draft)


def find_curation_item(item_id):
    """Look up a Platts curation item by id in staging -> today/yesterday archive."""
    from execution.curation import redis_client
    try:
        item = redis_client.get_staging(item_id)
    except Exception as exc:
        logger.warning(f"reprocess staging lookup failed for {item_id}: {exc}")
        item = None
    if item is not None:
        return item
    now_utc = datetime.now(timezone.utc)
    for offset in (0, 1):
        date = (now_utc - timedelta(days=offset)).strftime("%Y-%m-%d")
        try:
            item = redis_client.get_archive(date, item_id)
        except Exception as exc:
            logger.warning(f"reprocess archive lookup failed ({date}, {item_id}): {exc}")
            continue
        if item is not None:
            return item
    return None


async def _safe_edit(bot, text, chat_id, message_id):
    """Edit message text, ignoring 'message not modified' errors from Telegram."""
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id)
    except Exception as exc:
        if "not modified" not in str(exc).lower():
            logger.warning(f"edit_message failed: {exc}")


async def process_news(chat_id, raw_text, progress_msg_id):
    """Process news text through 3 agents as background task."""
    from execution.core.agents_progress import format_pipeline_progress
    from pipeline import run_3_agents

    bot = get_bot()
    phase_order = ["Writer", "Reviewer", "Finalizer"]
    done = []

    async def hook(phase_name):
        idx = phase_order.index(phase_name)
        done.clear()
        done.extend(phase_order[:idx])
        if progress_msg_id:
            await _safe_edit(
                bot,
                format_pipeline_progress(current=phase_name, done=list(done)),
                chat_id, progress_msg_id,
            )

    try:
        final_message = await run_3_agents(raw_text, on_phase_start=hook)

        draft_id = f"news_{int(time.time())}"
        drafts_set(draft_id, {
            "message": final_message,
            "status": "pending",
            "original_text": raw_text,
            "uazapi_token": None,
            "uazapi_url": None,
        })

        if progress_msg_id:
            await _safe_edit(
                bot,
                format_pipeline_progress(current=None, done=list(phase_order)),
                chat_id, progress_msg_id,
            )

        display = final_message[:3500] if len(final_message) > 3500 else final_message
        await bot.send_message(
            chat_id,
            f"📋 *PREVIEW*\n\n{display}",
            reply_markup=build_approval_keyboard(draft_id),
        )

    except Exception as e:
        logger.error(f"process_news failed: {e}")
        if progress_msg_id:
            remaining = [p for p in phase_order if p not in done]
            current = remaining[0] if remaining else None
            await _safe_edit(
                bot,
                format_pipeline_progress(current=current, done=list(done), error=str(e)[:120]),
                chat_id, progress_msg_id,
            )


async def process_adjustment(chat_id, draft_id, feedback):
    """Adjust draft with user feedback as background task."""
    from pipeline import run_adjuster
    bot = get_bot()
    progress = await bot.send_message(chat_id, "⏳ Ajustando mensagem...")
    progress_msg_id = progress.message_id

    try:
        draft = drafts_get(draft_id)
        if not draft:
            await bot.send_message(chat_id, "❌ Draft não encontrado.")
            return

        adjusted = await run_adjuster(draft["message"], feedback, draft["original_text"])

        draft["message"] = adjusted
        draft["status"] = "pending"
        drafts_set(draft_id, draft)

        await bot.edit_message_text("✅ Ajuste concluído!", chat_id=chat_id, message_id=progress_msg_id)

        display = adjusted[:3500] if len(adjusted) > 3500 else adjusted
        await bot.send_message(
            chat_id,
            f"📋 *PREVIEW*\n\n{display}",
            reply_markup=build_approval_keyboard(draft_id),
        )
        logger.info(f"Draft {draft_id} adjusted")
    except Exception as e:
        logger.error(f"Adjustment error: {e}")
        await bot.edit_message_text(f"❌ Erro no ajuste:\n{str(e)[:500]}", chat_id=chat_id, message_id=progress_msg_id)


async def run_pipeline_and_archive(chat_id, raw_text, progress_msg_id, item_id):
    """Run pipeline then archive staging item on success."""
    from execution.curation import redis_client
    try:
        await process_news(chat_id, raw_text, progress_msg_id)
    except Exception as exc:
        logger.error(f"pipeline failed for {item_id}: {exc}")
        bot = get_bot()
        try:
            await bot.edit_message_text(
                f"❌ Pipeline falhou\n\n`{str(exc)[:200]}`",
                chat_id=chat_id,
                message_id=progress_msg_id,
            )
        except Exception:
            pass
        return
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        await asyncio.to_thread(redis_client.archive, item_id, date, chat_id=chat_id)
    except Exception as exc:
        logger.warning(f"archive post-success failed for {item_id}: {exc}")
