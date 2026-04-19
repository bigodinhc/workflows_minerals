"""aiohttp routes for GitHub Actions endpoints and admin operations.

These are plain HTTP routes — not Telegram handlers. They serve:
- POST /store-draft (GitHub Actions -> store a draft for approval)
- GET/POST /seen-articles (GitHub Actions -> dedup for market_news)
- GET /health (monitoring)
- GET /test-ai (Anthropic API connectivity test)
- POST /admin/register-commands (register bot commands with Telegram)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import aiohttp
from aiohttp import web
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

import metrics  # noqa: F401 — side-effect: registers counters at module load
from bot.config import ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, UAZAPI_TOKEN, UAZAPI_URL
from bot.routers._helpers import drafts_set
import contact_admin

logger = logging.getLogger(__name__)

# In-memory state for seen articles (ephemeral, not worth Redis for 3d TTL)
SEEN_ARTICLES: dict = {}

routes = web.RouteTableDef()


@routes.get("/health")
async def health(request: web.Request) -> web.Response:
    return web.json_response({
        "status": "ok",
        "seen_articles_dates": len(SEEN_ARTICLES),
        "uazapi_token_set": bool(UAZAPI_TOKEN),
        "uazapi_url": UAZAPI_URL,
        "anthropic_key_set": bool(ANTHROPIC_API_KEY),
        "anthropic_key_prefix": ANTHROPIC_API_KEY[:10] + "..." if ANTHROPIC_API_KEY else "NONE",
    })


@routes.get("/metrics")
async def metrics_endpoint(request: web.Request) -> web.Response:
    """Prometheus scrape endpoint. Unauthenticated — counters are aggregate and non-sensitive."""
    return web.Response(
        body=generate_latest(),
        headers={"Content-Type": CONTENT_TYPE_LATEST},
    )


@routes.get("/test-ai")
async def test_ai(request: web.Request) -> web.Response:
    if not ANTHROPIC_API_KEY:
        return web.json_response({"error": "ANTHROPIC_API_KEY not set"}, status=500)
    try:
        from pipeline import call_claude
        result = await call_claude("You are helpful.", "Say 'hello' in one word.")
        return web.json_response({"status": "ok", "response": result[:100]})
    except Exception as e:
        return web.json_response(
            {"status": "error", "error_type": type(e).__name__, "error": str(e)[:500]},
            status=500,
        )


@routes.post("/store-draft")
async def store_draft(request: web.Request) -> web.Response:
    data = await request.json()
    draft_id = data.get("draft_id")
    message = data.get("message")
    if not draft_id or not message:
        return web.json_response({"error": "Missing draft_id or message"}, status=400)

    workflow_type = data.get("workflow_type")
    direct_delivery = data.get("direct_delivery", False)

    draft = {
        "message": message,
        "status": "pending",
        "original_text": "",
        "uazapi_token": (data.get("uazapi_token") or "").strip() or None,
        "uazapi_url": (data.get("uazapi_url") or "").strip() or None,
        "workflow_type": workflow_type,
        "direct_delivery": direct_delivery,
    }
    drafts_set(draft_id, draft)

    if draft["uazapi_token"]:
        logger.info(f"Draft includes UAZAPI token: {draft['uazapi_token'][:8]}...")
    else:
        logger.info(f"Draft has no UAZAPI token, will use env var")

    logger.info(f"Draft stored: {draft_id} ({len(message)} chars, workflow={workflow_type}, direct={direct_delivery})")

    # Telegram delivery to subscribers (non-blocking)
    telegram_result = None
    if direct_delivery and workflow_type:
        from bot.delivery import deliver_to_subscribers
        try:
            telegram_result = await deliver_to_subscribers(workflow_type, message)
            logger.info(f"Telegram delivery: {telegram_result}")
        except Exception as exc:
            logger.error(f"Telegram delivery failed: {exc}")
            telegram_result = {"sent": 0, "failed": 0, "error": str(exc)}

    response = {"success": True, "draft_id": draft_id}
    if telegram_result:
        response["telegram_delivery"] = telegram_result
    return web.json_response(response)


@routes.get("/seen-articles")
async def get_seen_articles(request: web.Request) -> web.Response:
    date = request.query.get("date", "")
    if not date:
        return web.json_response({"error": "Missing 'date' query parameter"}, status=400)
    titles = list(SEEN_ARTICLES.get(date, set()))
    return web.json_response({"date": date, "titles": titles})


@routes.post("/seen-articles")
async def store_seen_articles(request: web.Request) -> web.Response:
    data = await request.json()
    date = data.get("date", "")
    titles = data.get("titles", [])
    if not date or not titles:
        return web.json_response({"error": "Missing 'date' or 'titles'"}, status=400)

    if date not in SEEN_ARTICLES:
        SEEN_ARTICLES[date] = set()
    SEEN_ARTICLES[date].update(titles)

    # Prune entries older than 3 days
    try:
        cutoff = datetime.now() - timedelta(days=3)
        stale_keys = [k for k in SEEN_ARTICLES if datetime.strptime(k, "%Y-%m-%d") < cutoff]
        for k in stale_keys:
            del SEEN_ARTICLES[k]
    except ValueError as e:
        logger.warning(f"Date format mismatch during seen-articles pruning: {e}")

    logger.info(f"Stored {len(titles)} seen articles for {date} (total: {len(SEEN_ARTICLES.get(date, []))})")
    return web.json_response({"success": True, "stored": len(titles)})


@routes.post("/admin/register-commands")
async def register_commands(request: web.Request) -> web.Response:
    raw_chat_id = request.query.get("chat_id", "")
    try:
        chat_id = int(raw_chat_id)
    except ValueError:
        return web.json_response({"ok": False, "error": "chat_id query param required"}, status=400)
    if not contact_admin.is_authorized(chat_id):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=403)

    if not TELEGRAM_BOT_TOKEN:
        return web.json_response({"ok": False, "error": "TELEGRAM_BOT_TOKEN missing"}, status=500)

    commands = [
        {"command": "s", "description": "Menu principal com todos os atalhos"},
        {"command": "workflows", "description": "Disparar workflows (GitHub Actions)"},
        {"command": "reports", "description": "Consultar e baixar relatórios Platts (PDF)"},
        {"command": "help", "description": "Lista todos os comandos"},
        {"command": "queue", "description": "Items aguardando curadoria"},
        {"command": "history", "description": "Ultimos 10 arquivados"},
        {"command": "rejections", "description": "Ultimas 10 recusas"},
        {"command": "stats", "description": "Contadores de hoje"},
        {"command": "status", "description": "Saude dos workflows"},
        {"command": "reprocess", "description": "Re-dispara pipeline num item"},
        {"command": "add", "description": "Adicionar contato"},
        {"command": "list", "description": "Listar contatos"},
        {"command": "cancel", "description": "Abortar fluxo atual"},
    ]
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setMyCommands",
                json={"commands": commands},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                resp_data = await resp.json()
    except Exception as exc:
        logger.error(f"setMyCommands request failed: {exc}")
        return web.json_response({"ok": False, "error": str(exc)}, status=502)
    if not resp_data.get("ok"):
        logger.error(f"setMyCommands returned not-ok: {resp_data}")
        return web.json_response({"ok": False, "telegram": resp_data}, status=502)
    logger.info(f"setMyCommands registered {len(commands)} commands")
    return web.json_response({"ok": True, "registered": len(commands)})
