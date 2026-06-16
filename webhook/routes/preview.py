"""aiohttp route for /preview/{item_id} with Jinja2 template rendering."""

from __future__ import annotations

import logging

import aiohttp_jinja2
from aiohttp import web

logger = logging.getLogger(__name__)

routes = web.RouteTableDef()


@routes.get("/preview/{item_id}")
async def preview_item(request: web.Request) -> web.Response:
    """Render Platts item HTML preview for Telegram in-app browser."""
    from execution.curation import redis_client

    item_id = request.match_info["item_id"]
    item = None

    try:
        from execution.curation import news_repo
        item = redis_client.get_staging(item_id)
        if item is None:
            item = news_repo.get_by_id(item_id)
    except Exception as exc:
        logger.warning(f"Preview lookup failed: {exc}")

    if item is None:
        return web.Response(
            text=(
                "<!DOCTYPE html><html lang='pt-BR'><head><meta charset='UTF-8'>"
                "<title>Item não encontrado</title></head><body>"
                "<h1>Item não encontrado</h1>"
                "<p>Expirou (48h) ou já foi processado.</p>"
                "</body></html>"
            ),
            content_type="text/html",
            status=404,
        )

    safe_item = dict(item)
    if not isinstance(safe_item.get("fullText"), str):
        safe_item["fullText"] = ""
    if not isinstance(safe_item.get("tables"), list):
        safe_item["tables"] = []

    return aiohttp_jinja2.render_template("preview.html", request, {"item": safe_item})
