"""Classify dataset items and stage them in Redis with a `type` tag.

Post-v1.1: This module no longer posts to Telegram nor dispatches
rationale automatically. It stages everything in `platts:staging:*`
with a `type` field (`"news"` | `"rationale"`) so the caller can
send a single ingestion digest and the operator can review each item
manually via /queue.
"""
import re
from typing import List, Optional, Tuple

from execution.core.logger import WorkflowLogger
from execution.curation import redis_client
from execution.curation.id_gen import generate_id

_RATIONALE_TAB_RE = re.compile(r"\b(Rationale|Lump)\b", re.IGNORECASE)


def classify(item: dict) -> str:
    """Return 'rationale' for RMW Rationale/Lump items, 'curation' otherwise."""
    source = item.get("source") or ""
    tab_name = item.get("tabName") or ""
    if source.startswith("rmw") and _RATIONALE_TAB_RE.search(tab_name):
        return "rationale"
    return "curation"


def _type_tag(item: dict) -> str:
    """Return the staging type tag: 'rationale' → 'rationale', else 'news'."""
    return "rationale" if classify(item) == "rationale" else "news"


def _stage_only(item: dict, item_id: str, item_type: str, today_date: str) -> dict:
    """Stage one item in Redis and mark seen + scraped. Returns the dict that was staged."""
    to_stage = {**item, "id": item_id, "type": item_type}
    redis_client.set_staging(item_id, to_stage)
    redis_client.mark_seen(item_id)
    redis_client.mark_scraped(today_date, item_id)
    return to_stage


def route_items(
    items: List[dict],
    today_date: str,
    today_br: str,
    logger: Optional[WorkflowLogger] = None,
) -> Tuple[dict, List[dict]]:
    """Classify + stage every dataset item. Returns (counters, staged_items).

    counters keys: total, staged, news_staged, rationale_staged, skipped_seen,
    skipped_staged, skipped_invalid.
    staged_items: list of dicts actually written to Redis.
    """
    log = logger or WorkflowLogger("CurationRouter")
    counters = {
        "total": len(items),
        "staged": 0,
        "news_staged": 0,
        "rationale_staged": 0,
        "skipped_seen": 0,
        "skipped_staged": 0,
        "skipped_invalid": 0,
    }
    staged: List[dict] = []

    for item in items:
        item_type = _type_tag(item)
        try:
            item_id = generate_id(item.get("title", ""))
        except ValueError:
            counters["skipped_invalid"] += 1
            log.warning(f"Skipped item with empty/invalid title: {item.get('source', '?')}")
            continue
        if redis_client.staging_exists(item_id):
            counters["skipped_staged"] += 1
            continue
        if redis_client.is_seen(item_id):
            counters["skipped_seen"] += 1
            continue
        staged_item = _stage_only(item, item_id, item_type, today_date)
        staged.append(staged_item)
        counters["staged"] += 1
        if item_type == "rationale":
            counters["rationale_staged"] += 1
        else:
            counters["news_staged"] += 1

    log.info(f"Staged {counters['staged']} items "
             f"({counters['news_staged']} news, {counters['rationale_staged']} rationale); "
             f"{counters['skipped_seen']} skipped as seen, "
             f"{counters['skipped_staged']} skipped in staging, "
             f"{counters['skipped_invalid']} skipped invalid")
    return counters, staged
