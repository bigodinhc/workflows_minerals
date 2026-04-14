"""Classify dataset items and dispatch them to rationale AI or Telegram curation."""
import re
from typing import Callable, List

from execution.core.logger import WorkflowLogger
from execution.curation import redis_client
from execution.curation.id_gen import generate_id
from execution.curation.telegram_poster import post_for_curation as _post_for_curation

_RATIONALE_TAB_RE = re.compile(r"Rationale|Lump", re.IGNORECASE)


def classify(item: dict) -> str:
    """Return 'rationale' for RMW Rationale/Lump items, 'curation' otherwise."""
    source = item.get("source") or ""
    tab_name = item.get("tabName") or ""
    if source.startswith("rmw") and _RATIONALE_TAB_RE.search(tab_name):
        return "rationale"
    return "curation"


def _stage_and_post(item: dict, today_date: str, chat_id: int, preview_base_url: str, logger: WorkflowLogger) -> None:
    """Stage one curation item in Redis + post Telegram message."""
    item_id = generate_id(item.get("source", ""), item.get("title", ""))
    if redis_client.is_seen(today_date, item_id):
        logger.info(f"Skipping seen item {item_id} ({item.get('title','')[:40]})")
        return
    item = dict(item)
    item["id"] = item_id
    redis_client.set_staging(item_id, item)
    redis_client.mark_seen(today_date, item_id)
    try:
        _post_for_curation(chat_id=chat_id, item=item, preview_base_url=preview_base_url)
    except Exception as exc:
        logger.warning(f"Telegram post failed for {item_id}: {exc}")


def route_items(
    items: List[dict],
    today_date: str,
    today_br: str,
    chat_id: int,
    preview_base_url: str,
    rationale_processor: Callable[[List[dict], str], bool],
    logger: WorkflowLogger = None,
) -> dict:
    """Split items into rationale/curation buckets and dispatch each.

    rationale_processor: callable(items, today_br) -> bool (True on success).
    Returns counters dict {'total', 'rationale_processed', 'curation_posted', 'skipped_seen'}.
    """
    log = logger or WorkflowLogger("CurationRouter")
    counters = {"total": len(items), "rationale_processed": 0, "curation_posted": 0, "skipped_seen": 0}

    rationale_items: List[dict] = []
    curation_items: List[dict] = []
    for item in items:
        if classify(item) == "rationale":
            rationale_items.append(item)
        else:
            curation_items.append(item)

    # Rationale path: gated by daily flag
    if rationale_items:
        if redis_client.is_rationale_processed(today_date):
            log.info(f"Rationale already processed for {today_date}; skipping {len(rationale_items)} items.")
        else:
            log.info(f"Processing {len(rationale_items)} rationale items...")
            ok = rationale_processor(rationale_items, today_br)
            if ok:
                redis_client.set_rationale_processed(today_date)
                counters["rationale_processed"] = len(rationale_items)

    # Curation path: one Telegram message per new item
    for item in curation_items:
        item_id = generate_id(item.get("source", ""), item.get("title", ""))
        if redis_client.is_seen(today_date, item_id):
            counters["skipped_seen"] += 1
            continue
        _stage_and_post(item, today_date, chat_id, preview_base_url, log)
        counters["curation_posted"] += 1

    return counters
