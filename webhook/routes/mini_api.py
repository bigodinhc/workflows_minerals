"""Telegram Mini App API endpoints.

All routes require valid Telegram initData in X-Telegram-Init-Data header.
Prefix: /api/mini/
"""
from __future__ import annotations

import logging

from aiohttp import web

from routes.mini_auth import validate_init_data

logger = logging.getLogger(__name__)

routes = web.RouteTableDef()
