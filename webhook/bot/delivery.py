"""Telegram delivery to subscribed users.

Sends workflow content to all approved users who have the matching
subscription enabled. Used by /store-draft when direct_delivery=true
(internal workflows only — client workflows go to the channel via
bot.channel_delivery).
"""

from __future__ import annotations

import asyncio
import logging

from aiogram.exceptions import TelegramRetryAfter

from bot.config import get_bot
from bot.users import get_subscribers_for_workflow

logger = logging.getLogger(__name__)


async def _send_with_flood_retry(bot, chat_id: int, message: str) -> None:
    """Send a DM; on flood-wait sleep retry_after and retry once."""
    try:
        await bot.send_message(chat_id, message)
    except TelegramRetryAfter as exc:
        logger.warning(f"flood-wait for {chat_id}: sleeping {exc.retry_after}s")
        await asyncio.sleep(exc.retry_after)
        await bot.send_message(chat_id, message)


async def deliver_to_subscribers(workflow_type: str, message: str) -> dict:
    """Send message to all subscribers of workflow_type.

    Returns {"sent": int, "failed": int, "errors": list[str]}
    """
    bot = get_bot()
    subscribers = get_subscribers_for_workflow(workflow_type)

    sent = 0
    failed = 0
    errors = []

    for user in subscribers:
        chat_id = user["chat_id"]
        try:
            await _send_with_flood_retry(bot, chat_id, message)
            sent += 1
        except Exception as exc:
            failed += 1
            errors.append(f"{chat_id}: {str(exc)[:100]}")
            logger.warning(f"Telegram delivery failed for {chat_id}: {exc}")

    logger.info(f"Telegram delivery [{workflow_type}]: {sent} sent, {failed} failed")
    return {"sent": sent, "failed": failed, "errors": errors}
