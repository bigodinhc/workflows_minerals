"""Entry point: aiohttp app with Aiogram webhook handler.

Run: python -m webhook.bot.main
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import aiohttp_jinja2
import jinja2
from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# Ensure webhook/ is on sys.path (same as Dockerfile COPY layout)
_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))
# Also add repo root for execution.* imports in local dev
sys.path.insert(0, str(_HERE.parent))

from bot.config import (
    get_bot, get_dispatcher,
    WEBAPP_HOST, WEBAPP_PORT, WEBHOOK_PATH, TELEGRAM_WEBHOOK_URL,
    TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY, UAZAPI_URL, UAZAPI_TOKEN,
)
from bot.routers.onboarding import onboarding_router
from bot.routers.commands import public_router, admin_router, shared_router
from bot.routers.callbacks import callback_router
from bot.routers.messages import message_router, reply_kb_router
from routes.api import routes as api_routes
from routes.preview import routes as preview_routes
from routes.mini_api import routes as mini_api_routes
from routes.mini_static import routes as mini_static_routes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Background task tracking ──
_background_tasks: set = set()


def create_background_task(coro):
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


async def on_startup(app: web.Application):
    bot = get_bot()
    webhook_url = f"{TELEGRAM_WEBHOOK_URL}{WEBHOOK_PATH}"
    await bot.set_webhook(webhook_url)
    logger.info(f"Webhook set to {webhook_url}")

    # Log config
    logger.info(f"UAZAPI_URL: {UAZAPI_URL}")
    logger.info(f"UAZAPI_TOKEN: {'SET (' + UAZAPI_TOKEN[:8] + '...)' if UAZAPI_TOKEN else 'NOT SET'}")
    logger.info(f"TELEGRAM_BOT_TOKEN: {'SET' if TELEGRAM_BOT_TOKEN else 'NOT SET'}")
    logger.info(f"ANTHROPIC_API_KEY: {'SET' if ANTHROPIC_API_KEY else 'NOT SET'}")


async def on_shutdown(app: web.Application):
    bot = get_bot()
    await bot.delete_webhook()
    await bot.session.close()
    logger.info("Bot shut down cleanly")


def create_app() -> web.Application:
    # Dispatcher + routers
    dp = get_dispatcher()
    dp.include_router(onboarding_router)   # /start + approval + subscription (public)
    dp.include_router(public_router)        # other public commands
    dp.include_router(admin_router)         # admin-only commands
    dp.include_router(shared_router)        # /settings, /menu (admin + subscriber)
    dp.include_router(callback_router)      # inline button callbacks (admin)
    dp.include_router(reply_kb_router)      # reply keyboard text (admin + subscriber)
    dp.include_router(message_router)       # FSM + catch-all text (admin)

    # aiohttp app
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    # Jinja2 for preview template
    templates_dir = str(_HERE / "templates")
    aiohttp_jinja2.setup(app, loader=jinja2.FileSystemLoader(templates_dir))

    # Mount aiohttp routes
    app.router.add_routes(api_routes)
    app.router.add_routes(preview_routes)
    app.router.add_routes(mini_api_routes)
    app.router.add_routes(mini_static_routes)

    # Mount Aiogram webhook handler
    bot = get_bot()
    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path=WEBHOOK_PATH)

    return app


def main():
    app = create_app()
    web.run_app(app, host=WEBAPP_HOST, port=WEBAPP_PORT)


if __name__ == "__main__":
    main()
