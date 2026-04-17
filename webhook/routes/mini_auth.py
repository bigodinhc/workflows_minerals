"""Telegram Mini App initData authentication.

Validates the X-Telegram-Init-Data header using HMAC-SHA256 per
https://core.telegram.org/bots/webapps#validating-data-received-via-the-web-app

Uses aiogram.utils.web_app which provides check_webapp_signature()
and safe_parse_webapp_init_data().
"""
from __future__ import annotations

import logging

from aiohttp import web
from aiogram.utils.web_app import safe_parse_webapp_init_data, WebAppInitData

from bot.config import TELEGRAM_BOT_TOKEN
from bot.users import get_user_role

logger = logging.getLogger(__name__)


async def validate_init_data(request) -> WebAppInitData:
    """Extract and validate Telegram initData from request header.

    Returns WebAppInitData on success.
    Raises HTTPUnauthorized (missing/invalid) or HTTPForbidden (unauthorized user).
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    if not init_data:
        raise web.HTTPUnauthorized(text="Missing initData")

    try:
        data = safe_parse_webapp_init_data(TELEGRAM_BOT_TOKEN, init_data)
    except ValueError:
        raise web.HTTPUnauthorized(text="Invalid initData signature")

    if data.user:
        role = get_user_role(data.user.id)
        if role not in ("admin", "subscriber"):
            raise web.HTTPForbidden(text="Not authorized")

    return data
