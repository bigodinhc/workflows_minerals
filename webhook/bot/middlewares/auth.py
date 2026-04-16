"""Role-aware authorization middleware.

Replaces the binary AdminAuthMiddleware with a configurable RoleMiddleware
that accepts a set of allowed roles. Uses bot.users.get_user_role() to
determine the user's role (admin, subscriber, pending, unknown).

Usage:
  admin_router.message.middleware(RoleMiddleware(allowed_roles={"admin"}))
  shared_router.message.middleware(RoleMiddleware(allowed_roles={"admin", "subscriber"}))
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Set

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from bot.users import get_user_role

logger = logging.getLogger(__name__)


class RoleMiddleware(BaseMiddleware):
    def __init__(self, allowed_roles: Set[str]):
        self.allowed_roles = allowed_roles
        super().__init__()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        from_user = getattr(event, "from_user", None)
        if from_user is None:
            return await handler(event, data)

        role = get_user_role(from_user.id)
        if role not in self.allowed_roles:
            logger.debug(f"Role '{role}' not in {self.allowed_roles} for chat_id={from_user.id}")
            return None

        data["user_role"] = role
        return await handler(event, data)


# Backward compat: factory function that returns a RoleMiddleware configured for admin-only
def AdminAuthMiddleware():
    return RoleMiddleware(allowed_roles={"admin"})
