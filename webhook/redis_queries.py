"""Redis query helpers for webhook bot navigation and feedback.

Complements execution.curation.redis_client by adding read-side queries
(list, count, stats) and a new feedback keyspace. Kept separate from
curation to preserve contact_admin.py-style modularity.

Keyspaces used:
- platts:staging:<id>               (read)
- platts:archive:<date>:<id>        (read)
- platts:seen:<date>                (read, for stats)
- platts:pipeline:processed:<date>  (read + write, new)
- webhook:feedback:<ts>-<id>        (read + write, new Hash)
- webhook:feedback:index            (read + write, new Sorted Set)

All functions take an injectable redis client via _get_client() so tests
can swap in fakeredis. Same pattern as execution.curation.redis_client.
"""
import json
import os
from typing import Optional

_FEEDBACK_TTL_SECONDS = 30 * 24 * 60 * 60   # 30 days
_PIPELINE_TTL_SECONDS = 2 * 24 * 60 * 60    # 2 days

_client = None


def _get_client():
    """Return a cached Redis client using REDIS_URL."""
    global _client
    if _client is not None:
        return _client
    import redis
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        raise RuntimeError("REDIS_URL env var not set")
    _client = redis.Redis.from_url(
        url,
        socket_connect_timeout=3,
        socket_timeout=3,
        decode_responses=True,
    )
    return _client


def list_staging(limit: int = 50) -> list[dict]:
    """Return staging items newest-first, up to limit.

    Each dict contains the full parsed JSON plus an 'id' field extracted
    from the key suffix (in case the stored payload lacks it).
    """
    client = _get_client()
    keys = list(client.scan_iter(match="platts:staging:*", count=200))
    items: list[dict] = []
    for key in keys:
        raw = client.get(key)
        if raw is None:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        item_id = key.rsplit(":", 1)[-1]
        data.setdefault("id", item_id)
        items.append(data)
    items.sort(key=lambda d: d.get("stagedAt") or d.get("createdAt") or "", reverse=True)
    return items[:limit]
