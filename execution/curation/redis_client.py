"""Redis keyspaces for Platts curation.

Keyspaces:
- platts:staging:<id>               JSON string, TTL 48h
- platts:archive:<date>:<id>        JSON string, no TTL (consumed by other project)
- platts:seen                       Sorted Set, score=epoch, rolling 30d dedup
- platts:scraped:<date>             Set of ids, TTL 30d (daily telemetry)
- platts:rationale:processed:<date> String flag, TTL 30h (1x/day gate)

All functions use REDIS_URL env var via _get_client(). Tests monkeypatch _get_client.
"""
import json
import os
from datetime import datetime, timezone
from typing import Optional

_STAGING_TTL_SECONDS = 48 * 60 * 60           # 48h
_SEEN_TTL_SECONDS = 30 * 24 * 60 * 60         # 30d
_RATIONALE_FLAG_TTL_SECONDS = 30 * 60 * 60    # 30h

_client = None


def _get_client():
    """Return a cached Redis client using REDIS_URL.

    Raises RuntimeError if REDIS_URL is unset.

    Unlike state_store.py (which silently no-ops on Redis failure for
    observability workflows), this module raises because curation state
    (staging/archive) is load-bearing: losing a staged item silently
    would be worse than crashing the ingestion run.

    Connect and socket timeouts are 3s to prevent hanging the Telegram
    webhook handler on an unreachable Redis.
    """
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


def _staging_key(item_id: str) -> str:
    return f"platts:staging:{item_id}"


def _archive_key(date: str, item_id: str) -> str:
    return f"platts:archive:{date}:{item_id}"


_SEEN_KEY = "platts:seen"


def _rationale_flag_key(date: str) -> str:
    return f"platts:rationale:processed:{date}"


def set_staging(item_id: str, item: dict) -> None:
    """Persist item as JSON with 48h TTL.

    Injects stagedAt (UTC ISO8601) if not already present. The caller
    can pre-set it (e.g., reprocess flow that wants to preserve original
    staging time) and we will not overwrite.
    """
    item = dict(item)
    item.setdefault("stagedAt", datetime.now(timezone.utc).isoformat())
    client = _get_client()
    client.set(_staging_key(item_id), json.dumps(item, ensure_ascii=False), ex=_STAGING_TTL_SECONDS)


def get_staging(item_id: str) -> Optional[dict]:
    """Return item JSON or None if missing/expired."""
    client = _get_client()
    raw = client.get(_staging_key(item_id))
    if raw is None:
        return None
    return json.loads(raw)


def archive(item_id: str, date: str, chat_id: int) -> Optional[dict]:
    """Move item from staging to archive atomically.

    SET + DELETE run in a pipeline transaction so a mid-operation failure
    cannot leave the item in both keyspaces.

    Returns archived dict or None if staging missing.
    """
    item = get_staging(item_id)
    if item is None:
        return None
    item = dict(item)
    item["archivedAt"] = datetime.now(timezone.utc).isoformat()
    item["archivedBy"] = chat_id
    client = _get_client()
    pipe = client.pipeline(transaction=True)
    pipe.set(_archive_key(date, item_id), json.dumps(item, ensure_ascii=False))
    pipe.delete(_staging_key(item_id))
    pipe.execute()
    return item


def discard(item_id: str) -> None:
    """Delete staging without archiving."""
    client = _get_client()
    client.delete(_staging_key(item_id))


def get_archive(date: str, item_id: str) -> Optional[dict]:
    """Read an archived item by date + id. Returns None if missing."""
    client = _get_client()
    raw = client.get(_archive_key(date, item_id))
    if raw is None:
        return None
    return json.loads(raw)


def is_seen(item_id: str) -> bool:
    """Check if item id exists in global dedup sorted set."""
    client = _get_client()
    return client.zscore(_SEEN_KEY, item_id) is not None


def mark_seen(item_id: str) -> None:
    """Add id to global dedup sorted set. Prunes entries older than 30d."""
    import time
    client = _get_client()
    now = time.time()
    try:
        client.zremrangebyscore(_SEEN_KEY, "-inf", now - _SEEN_TTL_SECONDS)
    except Exception:
        pass
    client.zadd(_SEEN_KEY, {item_id: now})


def staging_exists(item_id: str) -> bool:
    """Check if a staging key exists for item_id."""
    client = _get_client()
    return bool(client.exists(_staging_key(item_id)))


def mark_scraped(date: str, item_id: str) -> None:
    """Add id to daily scraped set with 30d TTL. For /stats telemetry."""
    client = _get_client()
    key = f"platts:scraped:{date}"
    client.sadd(key, item_id)
    client.expire(key, _SEEN_TTL_SECONDS)


def is_rationale_processed(date: str) -> bool:
    """Check if rationale pipeline already ran for date."""
    client = _get_client()
    return client.get(_rationale_flag_key(date)) is not None


def set_rationale_processed(date: str) -> bool:
    """SET NX + EXPIRE — returns True if we set it (first time), False if already set."""
    client = _get_client()
    result = client.set(_rationale_flag_key(date), "1", nx=True, ex=_RATIONALE_FLAG_TTL_SECONDS)
    return bool(result)
