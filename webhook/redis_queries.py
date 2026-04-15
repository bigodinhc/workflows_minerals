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
import time
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


def list_archive_recent(limit: int = 10) -> list[dict]:
    """Return archived items newest-first across all dates, up to limit.

    Each dict contains the parsed JSON plus 'id' and 'archived_date'
    derived from the key structure platts:archive:<date>:<id>.
    """
    client = _get_client()
    keys = list(client.scan_iter(match="platts:archive:*", count=500))
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
        parts = key.split(":")
        if len(parts) < 4:
            continue
        archived_date = parts[2]
        item_id = parts[3]
        data.setdefault("id", item_id)
        data["archived_date"] = archived_date
        items.append(data)
    items.sort(key=lambda d: d.get("archivedAt") or "", reverse=True)
    return items[:limit]


def _feedback_key(feedback_id: str) -> str:
    return f"webhook:feedback:{feedback_id}"


def save_feedback(action: str, item_id: str, chat_id: int,
                  reason: str, title: str) -> str:
    """Create feedback Hash + index entry. Returns feedback_id '<ts>-<item_id>'."""
    client = _get_client()
    ts = time.time()
    feedback_id = f"{ts:.3f}-{item_id}"
    full_key = _feedback_key(feedback_id)
    pipe = client.pipeline(transaction=True)
    pipe.hset(full_key, mapping={
        "action": action,
        "item_id": item_id,
        "chat_id": str(chat_id),
        "reason": reason or "",
        "timestamp": f"{ts:.3f}",
        "title": title or "",
    })
    pipe.expire(full_key, _FEEDBACK_TTL_SECONDS)
    pipe.zadd("webhook:feedback:index", {feedback_id: ts})
    pipe.execute()
    return feedback_id


def update_feedback_reason(feedback_id: str, reason: str) -> bool:
    """Update reason field of an existing feedback Hash.

    Returns True if updated, False if the key doesn't exist.
    """
    client = _get_client()
    full_key = _feedback_key(feedback_id)
    if not client.exists(full_key):
        return False
    client.hset(full_key, "reason", reason or "")
    return True


def list_feedback(limit: int = 10,
                  action: Optional[str] = None,
                  since_ts: Optional[float] = None) -> list[dict]:
    """List feedback entries newest-first with optional filters.

    action: exact match filter on the 'action' field.
    since_ts: lower bound (inclusive) on the epoch timestamp.
    """
    client = _get_client()
    members = client.zrevrange("webhook:feedback:index", 0, -1, withscores=True)
    results: list[dict] = []
    for feedback_id, score in members:
        if since_ts is not None and score < since_ts:
            continue
        data = client.hgetall(_feedback_key(feedback_id))
        if not data:
            continue
        if action is not None and data.get("action") != action:
            continue
        data["feedback_id"] = feedback_id
        try:
            data["timestamp"] = float(data.get("timestamp") or 0)
        except ValueError:
            data["timestamp"] = 0.0
        try:
            data["chat_id"] = int(data.get("chat_id") or 0)
        except ValueError:
            data["chat_id"] = 0
        results.append(data)
        if len(results) >= limit:
            break
    return results
