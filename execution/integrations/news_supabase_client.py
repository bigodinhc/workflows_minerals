"""Client Supabase para a tabela de NOTÍCIAS (platts_news).

Resolve credenciais com fallback:
  NEWS_SUPABASE_URL / NEWS_SUPABASE_SERVICE_KEY  (override → banco pristine futuro)
  → senão SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY  (projeto liqiwvueesohlnnmezyw que o repo já usa)

Mantido separado de supabase_client.py para que mover as notícias a um projeto
dedicado no futuro seja só setar NEWS_*, sem tocar em código.
"""
from __future__ import annotations

import os

_client = None


def get_news_client():
    """Return a cached supabase Client for the news table.

    Prefers NEWS_SUPABASE_* (dedicated project); falls back to the repo's
    existing SUPABASE_* creds. Raises RuntimeError if neither is configured,
    so a misconfig fails loud instead of writing nowhere.
    """
    global _client
    if _client is not None:
        return _client
    from supabase import create_client
    url = (os.getenv("NEWS_SUPABASE_URL") or os.getenv("SUPABASE_URL") or "").strip()
    key = (os.getenv("NEWS_SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        raise RuntimeError(
            "No Supabase creds: set NEWS_SUPABASE_URL/NEWS_SUPABASE_SERVICE_KEY "
            "or SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY"
        )
    _client = create_client(url, key)
    return _client
