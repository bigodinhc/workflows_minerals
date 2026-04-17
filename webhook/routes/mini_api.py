"""Telegram Mini App API endpoints.

All routes require valid Telegram initData in X-Telegram-Init-Data header.
Prefix: /api/mini/
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import aiohttp
from aiohttp import web

import redis_queries
from execution.curation import redis_client
from routes.mini_auth import validate_init_data
from workflow_trigger import (
    WORKFLOW_CATALOG,
    _gh_headers,
    trigger_workflow,
    GITHUB_OWNER,
    GITHUB_REPO,
)

logger = logging.getLogger(__name__)

routes = web.RouteTableDef()

_GH_API = "https://api.github.com"

_WORKFLOW_ICONS = {
    "morning_check.yml": "\U0001f4ca",
    "baltic_ingestion.yml": "\u2693",
    "daily_report.yml": "\U0001f4c8",
    "market_news.yml": "\U0001f4f0",
    "platts_reports.yml": "\U0001f4c4",
}


def _run_duration(run: dict) -> int | None:
    if run.get("status") != "completed":
        return None
    started = run.get("run_started_at") or run.get("created_at")
    ended = run.get("updated_at")
    if not started or not ended:
        return None
    s = datetime.fromisoformat(started.replace("Z", "+00:00"))
    e = datetime.fromisoformat(ended.replace("Z", "+00:00"))
    return max(0, int((e - s).total_seconds()))


async def _fetch_github_runs(per_page: int = 100) -> dict:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{_GH_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/actions/runs",
                headers=_gh_headers(),
                params={"per_page": per_page},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
    except Exception as exc:
        logger.error("GitHub API error: %s", exc)
    return {"workflow_runs": []}


@routes.get("/api/mini/workflows")
async def get_workflows(request: web.Request) -> web.Response:
    await validate_init_data(request)

    data = await _fetch_github_runs()
    runs = data.get("workflow_runs", [])

    last_runs: dict[str, dict] = {}
    recent_by_wf: dict[str, list] = {}
    for run in runs:
        path = run.get("path", "")
        wf_id = path.split("/")[-1] if "/" in path else path
        if wf_id not in last_runs:
            last_runs[wf_id] = run
        if wf_id not in recent_by_wf:
            recent_by_wf[wf_id] = []
        if len(recent_by_wf[wf_id]) < 5:
            recent_by_wf[wf_id].append(run)

    workflows = []
    for wf in WORKFLOW_CATALOG:
        last = last_runs.get(wf["id"])
        recents = recent_by_wf.get(wf["id"], [])

        completed = [r for r in recents if r.get("status") == "completed"]
        successes = [r for r in completed if r.get("conclusion") == "success"]
        health = round(len(successes) / len(completed) * 100) if completed else 100

        last_run = None
        if last:
            last_run = {
                "status": last.get("status", "unknown"),
                "conclusion": last.get("conclusion"),
                "created_at": last.get("created_at"),
                "duration_seconds": _run_duration(last),
            }

        recent_runs_data = [
            {"conclusion": r.get("conclusion"), "created_at": r.get("created_at")}
            for r in recents
        ]

        workflows.append({
            "id": wf["id"],
            "name": wf["name"],
            "description": wf["description"],
            "icon": _WORKFLOW_ICONS.get(wf["id"], "\u2753"),
            "last_run": last_run,
            "health_pct": health,
            "recent_runs": recent_runs_data,
        })

    return web.json_response({"workflows": workflows})


@routes.get("/api/mini/workflows/{workflow_id}/runs")
async def get_workflow_runs(request: web.Request) -> web.Response:
    await validate_init_data(request)
    workflow_id = request.match_info["workflow_id"]
    limit = int(request.query.get("limit", "5"))

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{_GH_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/actions/workflows/{workflow_id}/runs",
                headers=_gh_headers(),
                params={"per_page": limit},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return web.json_response({"runs": []})
                data = await resp.json()
    except Exception as exc:
        logger.error("workflow runs API error: %s", exc)
        return web.json_response({"runs": []})

    runs = []
    for run in data.get("workflow_runs", [])[:limit]:
        runs.append({
            "id": run.get("id"),
            "status": run.get("status", "unknown"),
            "conclusion": run.get("conclusion"),
            "created_at": run.get("created_at"),
            "duration_seconds": _run_duration(run),
            "error": None,
            "html_url": run.get("html_url", ""),
        })

    return web.json_response({"runs": runs})


@routes.post("/api/mini/trigger")
async def trigger_workflow_endpoint(request: web.Request) -> web.Response:
    await validate_init_data(request)
    body = await request.json()
    workflow_id = body.get("workflow_id", "")
    if not workflow_id:
        return web.json_response(
            {"ok": False, "error": "Missing workflow_id"}, status=400,
        )

    ok, error = await trigger_workflow(workflow_id)
    if not ok:
        return web.json_response({"ok": False, "error": error}, status=502)
    return web.json_response({"ok": True})


# ── News endpoints ─────────────────────────────────────────────────────

_REJECT_ACTIONS = {"curate_reject", "draft_reject"}


def _staging_to_news_item(item: dict) -> dict:
    return {
        "id": item.get("id", ""),
        "title": item.get("title", ""),
        "source": item.get("source", ""),
        "source_feed": item.get("source_feed", ""),
        "date": item.get("publishDate", ""),
        "status": "pending",
        "preview_url": f"/preview/{item.get('id', '')}",
    }


def _archive_to_news_item(item: dict) -> dict:
    return {
        "id": item.get("id", ""),
        "title": item.get("title", ""),
        "source": item.get("source", ""),
        "source_feed": item.get("source_feed", ""),
        "date": item.get("publishDate", item.get("archivedAt", "")),
        "status": "archived",
        "preview_url": f"/preview/{item.get('id', '')}",
    }


def _feedback_to_news_item(item: dict) -> dict:
    ts = item.get("timestamp", 0)
    date = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else ""
    return {
        "id": item.get("item_id", ""),
        "title": item.get("title", ""),
        "source": "",
        "source_feed": "",
        "date": date,
        "status": "rejected",
        "preview_url": None,
    }


@routes.get("/api/mini/news")
async def get_news(request: web.Request) -> web.Response:
    await validate_init_data(request)
    status_filter = request.query.get("status", "all")
    page = int(request.query.get("page", "1"))
    limit = int(request.query.get("limit", "20"))

    items: list[dict] = []
    if status_filter in ("all", "pending"):
        staging = await asyncio.to_thread(redis_queries.list_staging, 500)
        items.extend(_staging_to_news_item(i) for i in staging)

    if status_filter in ("all", "archived"):
        archived = await asyncio.to_thread(redis_queries.list_archive_recent, 500)
        items.extend(_archive_to_news_item(i) for i in archived)

    if status_filter in ("all", "rejected"):
        feedback = await asyncio.to_thread(redis_queries.list_feedback, 500)
        rejected = [f for f in feedback if f.get("action") in _REJECT_ACTIONS]
        items.extend(_feedback_to_news_item(i) for i in rejected)

    items.sort(key=lambda x: x.get("date", ""), reverse=True)
    total = len(items)
    start = (page - 1) * limit
    page_items = items[start : start + limit]

    return web.json_response({
        "items": page_items,
        "total": total,
        "page": page,
    })


@routes.get("/api/mini/news/{item_id}")
async def get_news_detail(request: web.Request) -> web.Response:
    await validate_init_data(request)
    item_id = request.match_info["item_id"]

    item = await asyncio.to_thread(redis_client.get_staging, item_id)
    status = "pending"
    if item is None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        item = await asyncio.to_thread(redis_client.get_archive, today, item_id)
        status = "archived"
    if item is None:
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        item = await asyncio.to_thread(redis_client.get_archive, yesterday, item_id)
        status = "archived"
    if item is None:
        return web.json_response({"error": "Item not found"}, status=404)

    return web.json_response({
        "id": item_id,
        "title": item.get("title", ""),
        "source": item.get("source", ""),
        "source_feed": item.get("source_feed", ""),
        "date": item.get("publishDate", ""),
        "status": status,
        "fullText": item.get("fullText", ""),
        "tables": item.get("tables", []),
        "preview_url": f"/preview/{item_id}",
    })
