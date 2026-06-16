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
        "raw": item,
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
