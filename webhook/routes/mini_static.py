"""Serve Telegram Mini App static files from Vite dist/.

SPA fallback: any path under /mini/ that isn't a real file returns index.html.
"""
from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web

logger = logging.getLogger(__name__)

_DIST_DIR = Path(__file__).resolve().parent.parent / "mini-app" / "dist"

routes = web.RouteTableDef()


@routes.get("/mini/{path:.*}")
async def serve_mini_app(request: web.Request) -> web.Response:
    path = request.match_info.get("path", "")

    if path:
        file_path = (_DIST_DIR / path).resolve()
        if file_path.is_file() and str(file_path).startswith(str(_DIST_DIR)):
            return web.FileResponse(file_path)

    index = _DIST_DIR / "index.html"
    if index.is_file():
        return web.FileResponse(index)

    return web.Response(text="Mini App not built. Run: cd webhook/mini-app && npm run build", status=404)
