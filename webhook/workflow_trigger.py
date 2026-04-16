"""Trigger GitHub Actions workflows from Telegram with live status polling.

All functions are async — they use aiohttp for HTTP and Aiogram bot for Telegram.
"""

from __future__ import annotations

import asyncio
import logging
import os

import aiohttp

from bot.config import get_bot
from bot.callback_data import WorkflowRun, WorkflowList as WorkflowListCB

logger = logging.getLogger(__name__)

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_OWNER = os.getenv("GITHUB_OWNER", "bigodinhc")
GITHUB_REPO = os.getenv("GITHUB_REPO", "workflows_minerals")

WORKFLOW_CATALOG = [
    {"id": "morning_check.yml", "name": "MORNING CHECK", "description": "Precos Platts (Fines, Lump, Pellet, VIU)"},
    {"id": "baltic_ingestion.yml", "name": "BALTIC EXCHANGE", "description": "BDI + Rotas Capesize"},
    {"id": "daily_report.yml", "name": "DAILY SGX REPORT", "description": "Futuros SGX 62% Fe"},
    {"id": "market_news.yml", "name": "PLATTS INGESTION", "description": "Noticias Platts + curadoria"},
    {"id": "platts_reports.yml", "name": "PLATTS REPORTS", "description": "PDF reports scraping"},
]

_GH_API = "https://api.github.com"
_POLL_INTERVAL_SECONDS = 15
_POLL_TIMEOUT_SECONDS = 600


def _gh_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _workflow_name_by_id(workflow_id):
    for w in WORKFLOW_CATALOG:
        if w["id"] == workflow_id:
            return w["name"]
    return workflow_id


async def render_workflow_list():
    """Fetch last run per workflow, return (text, reply_markup dict)."""
    last_runs = {}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{_GH_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/actions/runs",
                headers=_gh_headers(),
                params={"per_page": 50},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for run in data.get("workflow_runs", []):
                        path = run.get("path", "")
                        wf_id = path.split("/")[-1] if "/" in path else path
                        if wf_id not in last_runs:
                            last_runs[wf_id] = run
    except Exception as exc:
        logger.error(f"render_workflow_list GitHub API error: {exc}")

    text = "⚡ *Workflows*\n\nEscolha um workflow para disparar:"
    keyboard = []
    for wf in WORKFLOW_CATALOG:
        run = last_runs.get(wf["id"])
        if run:
            conclusion = run.get("conclusion")
            if conclusion == "success":
                icon = "✅"
            elif conclusion == "failure":
                icon = "❌"
            elif run.get("status") == "in_progress":
                icon = "🔄"
            else:
                icon = "⏳"
        else:
            icon = "❓"
        label = f"{icon} {wf['name']}"
        keyboard.append([{"text": label, "callback_data": WorkflowRun(workflow_id=wf["id"]).pack()}])

    keyboard.append([{"text": "⬅ Menu", "callback_data": WorkflowListCB(action="back_menu").pack()}])
    markup = {"inline_keyboard": keyboard}
    return text, markup


async def trigger_workflow(workflow_id):
    """Dispatch a workflow run. Returns (ok, error)."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{_GH_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/actions/workflows/{workflow_id}/dispatches",
                headers=_gh_headers(),
                json={"ref": "main", "inputs": {"dry_run": "false"}},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 204:
                    return True, None
                body = await resp.text()
                return False, f"HTTP {resp.status}: {body[:100]}"
    except Exception as exc:
        logger.error(f"trigger_workflow error: {exc}")
        return False, str(exc)


async def find_triggered_run(workflow_id, max_wait=30):
    """Poll for a newly-created run. Returns run_id or None."""
    for _ in range(max_wait // 5):
        await asyncio.sleep(5)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{_GH_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/actions/workflows/{workflow_id}/runs",
                    headers=_gh_headers(),
                    params={"per_page": 1, "status": "in_progress"},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        runs = data.get("workflow_runs", [])
                        if runs:
                            return runs[0]["id"]
        except Exception as exc:
            logger.warning(f"find_triggered_run poll error: {exc}")
    return None


async def check_run_status(run_id):
    """Check a specific run. Returns (status, conclusion, html_url)."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{_GH_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/actions/runs/{run_id}",
                headers=_gh_headers(),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["status"], data.get("conclusion"), data.get("html_url", "")
    except Exception as exc:
        logger.error(f"check_run_status error: {exc}")
    return "unknown", None, ""


async def poll_and_update(chat_id, message_id, workflow_id, run_id):
    """Poll run status and edit Telegram message (async, no thread)."""
    bot = get_bot()
    name = _workflow_name_by_id(workflow_id)
    elapsed = 0

    while elapsed < _POLL_TIMEOUT_SECONDS:
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        elapsed += _POLL_INTERVAL_SECONDS

        status, conclusion, html_url = await check_run_status(run_id)

        if status == "completed":
            if conclusion == "success":
                icon, label = "✅", "concluido"
            else:
                icon, label = "❌", f"falhou ({conclusion})"

            buttons = {"inline_keyboard": [
                [{"text": "🔗 Ver no GitHub", "url": html_url}],
                [{"text": "⬅ Workflows", "callback_data": WorkflowListCB(action="list").pack()}],
            ]}
            await bot.edit_message_text(
                f"{icon} *{name}* {label}",
                chat_id=chat_id, message_id=message_id, reply_markup=buttons,
            )
            return

    await bot.edit_message_text(
        f"⏰ *{name}* — timeout (10min)\n\nVerifique no GitHub.",
        chat_id=chat_id, message_id=message_id,
        reply_markup={"inline_keyboard": [[
            {"text": "⬅ Workflows", "callback_data": WorkflowListCB(action="list").pack()},
        ]]},
    )
