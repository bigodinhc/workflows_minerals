"""Telegram delivery to subscribed users.

Sends workflow content to all approved users who have the matching
subscription enabled. Used by /store-draft when direct_delivery=true.
"""

from __future__ import annotations

import logging

from bot.config import get_bot
from bot.users import get_subscribers_for_workflow

logger = logging.getLogger(__name__)


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
            await bot.send_message(chat_id, message)
            sent += 1
        except Exception as exc:
            failed += 1
            errors.append(f"{chat_id}: {str(exc)[:100]}")
            logger.warning(f"Telegram delivery failed for {chat_id}: {exc}")

    logger.info(f"Telegram delivery [{workflow_type}]: {sent} sent, {failed} failed")
    return {"sent": sent, "failed": failed, "errors": errors}
