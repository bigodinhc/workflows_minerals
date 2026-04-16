"""Admin authorization middleware.

Applied to admin-only routers. Silently drops updates from unauthorized users.
The /start command lives on a separate public router without this middleware.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

import contact_admin

logger = logging.getLogger(__name__)


class AdminAuthMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        from_user = getattr(event, "from_user", None)
        if from_user is None:
            return await handler(event, data)

        chat_id = from_user.id
        if not contact_admin.is_authorized(chat_id):
            logger.debug(f"Unauthorized access attempt from chat_id={chat_id}")
            return None

        return await handler(event, data)
