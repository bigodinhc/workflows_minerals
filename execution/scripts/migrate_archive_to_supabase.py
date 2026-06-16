#!/usr/bin/env python3
"""Carga única: platts:archive:* (Redis) → platts_news (Supabase).

Idempotente: usa upsert ON CONFLICT DO NOTHING, então pode rodar N vezes.
Roda manualmente após a migração SQL estar aplicada no projeto de notícias.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from execution.curation import redis_client
from execution.curation.news_repo import _item_to_row, TABLE
from execution.integrations.news_supabase_client import get_news_client


def _archive_row(key: str, data: dict) -> dict:
    """Build a platts_news row (status=archived) from an archive payload."""
    item_id = data.get("id") or key.split(":")[-1]
    row = _item_to_row(item_id, data, status="archived")
    if data.get("archivedAt"):
        row["archived_at"] = data["archivedAt"]
    if data.get("archivedBy") is not None:
        row["archived_by"] = data["archivedBy"]
    if data.get("stagedAt"):
        row["scraped_at"] = data["stagedAt"]
    return row


def backfill() -> int:
    """Read all platts:archive:* keys and upsert them. Returns count processed."""
    client = redis_client._get_client()
    sb = get_news_client()
    count = 0
    for key in client.scan_iter(match="platts:archive:*", count=500):
        raw = client.get(key)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        row = _archive_row(key, data)
        sb.table(TABLE).upsert(row, on_conflict="id", ignore_duplicates=True).execute()
        count += 1
    return count


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
    n = backfill()
    print(f"✅ Backfill concluído: {n} itens processados")
