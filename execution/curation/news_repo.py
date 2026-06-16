"""Repositório Supabase da tabela platts_news.

Espelha a interface de curadoria do Redis (set_staging/archive/get_archive/...)
para minimizar o blast radius nos call sites. Toda escrita usa o client do
projeto de notícias (news_supabase_client.get_news_client).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from execution.integrations.news_supabase_client import get_news_client

TABLE = "platts_news"


def _item_to_row(item_id: str, item: dict, status: str = "staged") -> dict:
    """Map a scraper item dict to a platts_news row. Omits keys that should
    fall back to DB defaults (scraped_at)."""
    row = {
        "id": item_id,
        "type": item.get("type") or "news",
        "status": status,
        "title": item.get("title") or "",
        "href": item.get("href") or item.get("url"),
        "source": item.get("source"),
        "author": item.get("author"),
        "publish_date": item.get("publishDate") or item.get("date"),
        "full_text": item.get("fullText"),
        "paragraphs": item.get("paragraphs"),
        "tables": item.get("tables"),
        "metadata": item.get("metadata"),
        "raw": dict(item),
    }
    staged_at = item.get("stagedAt")
    if staged_at:
        row["scraped_at"] = staged_at
    return row


def upsert_scraped(item_id: str, item: dict) -> bool:
    """Idempotent insert at ingestion. ON CONFLICT (id) DO NOTHING.

    Returns True if a new row was inserted, False if it already existed.
    """
    row = _item_to_row(item_id, item, status="staged")
    resp = (
        get_news_client()
        .table(TABLE)
        .upsert(row, on_conflict="id", ignore_duplicates=True)
        .execute()
    )
    return bool(getattr(resp, "data", None))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def set_status(item_id: str, status: str, *,
               chat_id: Optional[int] = None, reason: Optional[str] = None) -> bool:
    """Update a row's status. Returns True if a row was updated.

    archived → stamps archived_at/archived_by. rejected → stamps rejected_at/reject_reason.
    """
    payload: dict = {"status": status}
    if status == "archived":
        payload["archived_at"] = _now_iso()
        if chat_id is not None:
            payload["archived_by"] = chat_id
    elif status == "rejected":
        payload["rejected_at"] = _now_iso()
        if reason is not None:
            payload["reject_reason"] = reason
    resp = get_news_client().table(TABLE).update(payload).eq("id", item_id).execute()
    return bool(getattr(resp, "data", None))


def set_status_bulk(item_ids: list[str], status: str, *,
                    chat_id: Optional[int] = None) -> int:
    """Update many rows' status in one query. Returns count of rows updated."""
    if not item_ids:
        return 0
    payload: dict = {"status": status}
    if status == "archived":
        payload["archived_at"] = _now_iso()
        if chat_id is not None:
            payload["archived_by"] = chat_id
    elif status == "rejected":
        payload["rejected_at"] = _now_iso()
    resp = get_news_client().table(TABLE).update(payload).in_("id", item_ids).execute()
    return len(getattr(resp, "data", None) or [])


def get_by_id(item_id: str) -> Optional[dict]:
    """Read a single row by id. Returns None if missing."""
    resp = (
        get_news_client().table(TABLE).select("*").eq("id", item_id).limit(1).execute()
    )
    data = getattr(resp, "data", None) or []
    return data[0] if data else None


def list_by_status(status: str, limit: int = 10) -> list[dict]:
    """List rows of a given status, newest archived/scraped first."""
    order_col = "archived_at" if status == "archived" else "scraped_at"
    resp = (
        get_news_client().table(TABLE).select("*").eq("status", status)
        .order(order_col, desc=True).limit(limit).execute()
    )
    return getattr(resp, "data", None) or []


def search(query: str, limit: int = 10) -> list[dict]:
    """Full-text search over title+full_text via the generated tsvector column."""
    resp = (
        get_news_client().table(TABLE).select("*")
        .text_search("fts", query, options={"type": "websearch", "config": "english"})
        .limit(limit).execute()
    )
    return getattr(resp, "data", None) or []
