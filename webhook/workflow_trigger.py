"""Trigger GitHub Actions workflows from Telegram with live status polling."""
import os
import logging
import threading
import time
import requests

from telegram import send_telegram_message, edit_message, answer_callback

logger = logging.getLogger(__name__)

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_OWNER = os.getenv("GITHUB_OWNER", "bigodinhc")
GITHUB_REPO = os.getenv("GITHUB_REPO", "workflows_minerals")

WORKFLOW_CATALOG = [
    {
        "id": "morning_check.yml",
        "name": "MORNING CHECK",
        "description": "Precos Platts (Fines, Lump, Pellet, VIU)",
    },
    {
        "id": "baltic_ingestion.yml",
        "name": "BALTIC EXCHANGE",
        "description": "BDI + Rotas Capesize",
    },
    {
        "id": "daily_report.yml",
        "name": "DAILY SGX REPORT",
        "description": "Futuros SGX 62% Fe",
    },
    {
        "id": "market_news.yml",
        "name": "PLATTS INGESTION",
        "description": "Noticias Platts + curadoria",
    },
    {
        "id": "platts_reports.yml",
        "name": "PLATTS REPORTS",
        "description": "PDF reports scraping",
    },
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


def render_workflow_list():
    """Fetch last run per workflow, return (text, reply_markup)."""
    last_runs = {}
    try:
        resp = requests.get(
            f"{_GH_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/actions/runs",
            headers=_gh_headers(),
            params={"per_page": 50},
            timeout=15,
        )
        if resp.status_code == 200:
            for run in resp.json().get("workflow_runs", []):
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
        keyboard.append([{"text": label, "callback_data": f"wf_run:{wf['id']}"}])

    keyboard.append([{"text": "⬅ Menu", "callback_data": "wf_back_menu"}])
    markup = {"inline_keyboard": keyboard}
    return text, markup


def trigger_workflow(workflow_id):
    """Dispatch a workflow run. Returns (ok, error)."""
    try:
        resp = requests.post(
            f"{_GH_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/actions/workflows/{workflow_id}/dispatches",
            headers=_gh_headers(),
            json={"ref": "main", "inputs": {"dry_run": "false"}},
            timeout=15,
        )
        if resp.status_code == 204:
            return True, None
        return False, f"HTTP {resp.status_code}: {resp.text[:100]}"
    except Exception as exc:
        logger.error(f"trigger_workflow error: {exc}")
        return False, str(exc)


def find_triggered_run(workflow_id, max_wait=30):
    """Poll for a newly-created run. Returns run_id or None."""
    for _ in range(max_wait // 5):
        time.sleep(5)
        try:
            resp = requests.get(
                f"{_GH_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/actions/workflows/{workflow_id}/runs",
                headers=_gh_headers(),
                params={"per_page": 1, "status": "in_progress"},
                timeout=15,
            )
            if resp.status_code == 200:
                runs = resp.json().get("workflow_runs", [])
                if runs:
                    return runs[0]["id"]
        except Exception as exc:
            logger.warning(f"find_triggered_run poll error: {exc}")
    return None


def check_run_status(run_id):
    """Check a specific run. Returns (status, conclusion, html_url)."""
    try:
        resp = requests.get(
            f"{_GH_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/actions/runs/{run_id}",
            headers=_gh_headers(),
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data["status"], data.get("conclusion"), data.get("html_url", "")
    except Exception as exc:
        logger.error(f"check_run_status error: {exc}")
    return "unknown", None, ""


def _poll_and_update(chat_id, message_id, workflow_id, run_id):
    """Background thread: poll run status and edit Telegram message."""
    name = _workflow_name_by_id(workflow_id)
    elapsed = 0

    while elapsed < _POLL_TIMEOUT_SECONDS:
        time.sleep(_POLL_INTERVAL_SECONDS)
        elapsed += _POLL_INTERVAL_SECONDS

        status, conclusion, html_url = check_run_status(run_id)

        if status == "completed":
            if conclusion == "success":
                icon, label = "✅", "concluido"
            else:
                icon, label = "❌", f"falhou ({conclusion})"

            buttons = {"inline_keyboard": [
                [{"text": "🔗 Ver no GitHub", "url": html_url}],
                [{"text": "⬅ Workflows", "callback_data": "wf_list"}],
            ]}
            edit_message(chat_id, message_id, f"{icon} *{name}* {label}", reply_markup=buttons)
            return

    edit_message(
        chat_id, message_id,
        f"⏰ *{name}* — timeout (10min)\n\nVerifique no GitHub.",
        reply_markup={"inline_keyboard": [[{"text": "⬅ Workflows", "callback_data": "wf_list"}]]},
    )


def handle_wf_callback(callback_data, chat_id, message_id, callback_id):
    """Handle all wf_* callbacks."""
    from flask import jsonify

    if callback_data == "wf_list":
        answer_callback(callback_id, "")
        text, markup = render_workflow_list()
        edit_message(chat_id, message_id, text, reply_markup=markup)
        return jsonify({"ok": True})

    if callback_data == "wf_back_menu":
        answer_callback(callback_id, "")
        from app import _show_main_menu
        _show_main_menu(chat_id)
        return jsonify({"ok": True})

    if callback_data.startswith("wf_run:"):
        workflow_id = callback_data.split(":", 1)[1]
        name = _workflow_name_by_id(workflow_id)
        answer_callback(callback_id, f"Disparando {name}...")

        edit_message(
            chat_id, message_id,
            f"🚀 *Disparando {name}...*",
            reply_markup={"inline_keyboard": [[{"text": "⬅ Cancelar", "callback_data": "wf_list"}]]},
        )

        ok, error = trigger_workflow(workflow_id)
        if not ok:
            edit_message(
                chat_id, message_id,
                f"❌ *{name}* — erro ao disparar\n\n`{error}`",
                reply_markup={"inline_keyboard": [
                    [{"text": "🔄 Tentar novamente", "callback_data": f"wf_run:{workflow_id}"}],
                    [{"text": "⬅ Workflows", "callback_data": "wf_list"}],
                ]},
            )
            return jsonify({"ok": True})

        edit_message(
            chat_id, message_id,
            f"🔄 *{name}* rodando...\n\nAguardando conclusao.",
            reply_markup=None,
        )

        def _track():
            run_id = find_triggered_run(workflow_id)
            if run_id is None:
                edit_message(
                    chat_id, message_id,
                    f"⚠️ *{name}* — disparado mas nao encontrei o run\n\nVerifique no GitHub.",
                    reply_markup={"inline_keyboard": [[{"text": "⬅ Workflows", "callback_data": "wf_list"}]]},
                )
                return
            _poll_and_update(chat_id, message_id, workflow_id, run_id)

        threading.Thread(target=_track, daemon=True).start()
        return jsonify({"ok": True})

    answer_callback(callback_id, "")
    return jsonify({"ok": True})
