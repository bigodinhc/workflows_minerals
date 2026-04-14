"""
State store: Redis-backed persistence of workflow run outcomes.

All functions are non-raising. When REDIS_URL is unset or Redis is
unreachable, writes are silent no-ops and reads return None. Workflows
must never be broken by this module.
"""
import json
import os
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    """Return a connected Redis client, or None if disabled/unavailable.
    Cached on first successful connection. Overridable in tests via
    monkeypatch on state_store._get_client."""
    global _client
    if _client is not None:
        return _client
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        return None
    try:
        import redis
        _client = redis.Redis.from_url(
            url,
            socket_connect_timeout=3,
            socket_timeout=3,
            decode_responses=True,
        )
        _client.ping()
    except Exception as exc:
        logger.warning(f"state_store: redis connection failed: {exc}")
        _client = None
    return _client


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _write_last_run(client, workflow: str, payload: dict) -> None:
    client.set(f"wf:last_run:{workflow}", json.dumps(payload))


def _push_failure(client, workflow: str, reason: str, time_iso: str) -> None:
    entry = json.dumps({"time": time_iso, "reason": reason})
    client.lpush(f"wf:failures:{workflow}", entry)
    client.ltrim(f"wf:failures:{workflow}", 0, 2)


def record_success(workflow: str, summary: dict, duration_ms: int) -> None:
    """Record a successful run. Clears failure streak."""
    client = _get_client()
    if client is None:
        return
    try:
        _write_last_run(client, workflow, {
            "status": "success",
            "time_iso": _now_iso(),
            "summary": summary,
            "duration_ms": duration_ms,
        })
        client.delete(f"wf:streak:{workflow}")
    except Exception as exc:
        logger.warning(f"state_store.record_success failed: {exc}")


def record_failure(workflow: str, summary: dict, duration_ms: int) -> None:
    """Record a delivery failure (100% failed). Increments streak."""
    client = _get_client()
    if client is None:
        return
    try:
        now = _now_iso()
        reason = f"0/{summary.get('total', 0)} enviadas"
        _write_last_run(client, workflow, {
            "status": "failure",
            "time_iso": now,
            "summary": summary,
            "duration_ms": duration_ms,
        })
        _push_failure(client, workflow, reason, now)
        client.incr(f"wf:streak:{workflow}")
    except Exception as exc:
        logger.warning(f"state_store.record_failure failed: {exc}")


def record_empty(workflow: str, reason: str) -> None:
    """Record a non-failure early-exit (e.g., 'no data yet'). Streak untouched."""
    client = _get_client()
    if client is None:
        return
    try:
        _write_last_run(client, workflow, {
            "status": "empty",
            "time_iso": _now_iso(),
            "reason": reason,
        })
    except Exception as exc:
        logger.warning(f"state_store.record_empty failed: {exc}")


def record_crash(workflow: str, exc_text: str) -> None:
    """Record a workflow crash (uncaught exception). Increments streak."""
    client = _get_client()
    if client is None:
        return
    try:
        now = _now_iso()
        _write_last_run(client, workflow, {
            "status": "crash",
            "time_iso": now,
            "reason": exc_text[:200],
        })
        _push_failure(client, workflow, f"crash: {exc_text[:120]}", now)
        client.incr(f"wf:streak:{workflow}")
    except Exception as exc:
        logger.warning(f"state_store.record_crash failed: {exc}")
