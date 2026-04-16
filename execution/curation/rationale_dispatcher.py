"""Dispatches rationale items to the rationale AI pipeline.

TODO (v1.1+): Este módulo ficou ÓRFÃO após Bot Navigation v1.1 —
o router não o chama mais automaticamente (rationale agora passa
pela curadoria manual como qualquer notícia). Mantemos o código aqui
porque pode ser útil como utilitário chamado manualmente via script
ou como base pra uma fase futura de prompts dedicados de rationale.
Revisitar pra possível remoção quando essa decisão for tomada.
"""
import json
import os
from datetime import datetime
from typing import List

from execution.agents.rationale_agent import RationaleAgent
from execution.core import state_store
from execution.core.logger import WorkflowLogger
from execution.integrations.telegram_client import TelegramClient

_WORKFLOW_NAME = "rationale_news"
_DRAFTS_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "news_drafts.json")


# NOT concurrency-safe — assumes cron serialization. Do not call from
# overlapping processes.
def _save_draft(draft: dict) -> None:
    """Append draft to news_drafts.json."""
    drafts_path = os.path.abspath(_DRAFTS_FILE)
    os.makedirs(os.path.dirname(drafts_path), exist_ok=True)
    if os.path.exists(drafts_path):
        with open(drafts_path, "r") as f:
            try:
                drafts = json.load(f)
            except (json.JSONDecodeError, ValueError):
                drafts = []
    else:
        drafts = []
    drafts.append(draft)
    with open(drafts_path, "w") as f:
        json.dump(drafts, f, indent=2, ensure_ascii=False)


def process(rationale_items: List[dict], today_br: str, logger: WorkflowLogger = None) -> bool:
    """Run full rationale pipeline on items.

    Returns True if processing succeeded end-to-end (caller should set the
    daily processed flag). Returns False on short-circuit (empty items or
    insufficient content) - caller should NOT set flag so retry can happen.
    Raises on unexpected errors (caller logs + decides on record_crash).
    """
    log = logger or WorkflowLogger("RationaleDispatcher")

    if not rationale_items:
        log.warning("No rationale items to process.")
        state_store.record_empty(_WORKFLOW_NAME, "sem rationales no run")
        return False

    combined_text = "\n\n".join([
        (
            f"=== ARTICLE {i+1} ===\n"
            f"Tab: {item.get('tabName', '')}\n"
            f"Title: {item.get('title')}\n"
            f"Date: {item.get('gridDateTime') or item.get('publishDate') or ''}\n\n"
            f"{item.get('fullText', '')}"
        )
        for i, item in enumerate(rationale_items)
    ])

    # Unlike the deprecated rationale_ingestion.py we do NOT generate a
    # "Sem Destaques Relevantes" placeholder draft here — caller decides
    # whether to dispatch a fallback message.
    if len(combined_text.strip()) < 200:
        log.warning(f"Combined rationale text too short ({len(combined_text)} chars). Skipping AI.")
        state_store.record_empty(_WORKFLOW_NAME, "conteudo insuficiente")
        return False

    log.info(f"Running RationaleAgent on {len(rationale_items)} items...")
    agent = RationaleAgent()
    draft_text = agent.process(combined_text, today_br)

    draft_obj = {
        "id": f"draft_{int(datetime.now().timestamp())}",
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "source_date": today_br,
        "original_count": len(rationale_items),
        "ai_text": draft_text,
        "source_summary": (rationale_items[0].get("title") or "Sem Título") + "...",
    }
    _save_draft(draft_obj)
    log.info("Draft saved.")

    webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL", "")
    # Best-effort: webhook store is only needed for Telegram-button approvals.
    # Dashboard approval works without it, so we tolerate failures.
    if webhook_url:
        import requests
        try:
            requests.post(
                f"{webhook_url}/store-draft",
                json={
                    "draft_id": draft_obj["id"],
                    "message": draft_text,
                    "uazapi_token": os.getenv("UAZAPI_TOKEN", ""),
                    "uazapi_url": os.getenv("UAZAPI_URL", "https://mineralstrading.uazapi.com"),
                    "workflow_type": "rationale_news",
                    "direct_delivery": False,
                },
                timeout=10,
            )
        except Exception as exc:
            log.warning(f"Could not store draft on webhook: {exc}")

    telegram = TelegramClient()
    telegram.send_approval_request(draft_id=draft_obj["id"], preview_text=draft_text)
    log.info("Telegram approval sent.")

    state_store.record_success(_WORKFLOW_NAME, {"total": 1, "success": 1, "failure": 0}, 0)
    return True
