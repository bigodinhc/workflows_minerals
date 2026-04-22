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
    """Record a delivery failure (100% failed). Increments streak. May trigger alert."""
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
        new_streak = client.incr(f"wf:streak:{workflow}")
    except Exception as exc:
        logger.warning(f"state_store.record_failure failed: {exc}")
        return
    if new_streak >= _STREAK_THRESHOLD:
        try:
            failures = client.lrange(f"wf:failures:{workflow}", 0, 2) or []
            _send_streak_alert(workflow, int(new_streak), failures)
        except Exception as exc:
            logger.warning(f"streak alert trigger failed: {exc}")


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


_CRASH_DEDUP_TTL_SECONDS = 300  # 5 min: dedup same-workflow crashes recorded twice


def record_crash(workflow: str, exc_text: str) -> None:
    """Record a workflow crash (uncaught exception). Increments streak. May trigger alert.

    Idempotent per workflow within a 5-minute window. The @with_event_bus decorator
    (execution/core/event_bus.py) and progress_reporter.fail() may both observe the
    same exception and both call record_crash — the SET NX dedup key ensures only the
    first write wins. Prevents double-counting the same crash as two streak increments.

    Dedup-key failure (Redis flaky mid-call) falls through to recording, since an
    extra alert is better than silently dropping a crash."""
    client = _get_client()
    if client is None:
        return
    try:
        dedup_key = f"wf:crash_dedup:{workflow}"
        claimed = client.set(dedup_key, "1", nx=True, ex=_CRASH_DEDUP_TTL_SECONDS)
        if not claimed:
            return  # already recorded within dedup window
    except Exception as exc:
        logger.warning(f"state_store.record_crash dedup check failed: {exc}")
        # Fall through — over-alerting beats silent drop
    try:
        now = _now_iso()
        _write_last_run(client, workflow, {
            "status": "crash",
            "time_iso": now,
            "reason": exc_text[:200],
        })
        _push_failure(client, workflow, f"crash: {exc_text[:120]}", now)
        new_streak = client.incr(f"wf:streak:{workflow}")
    except Exception as exc:
        logger.warning(f"state_store.record_crash failed: {exc}")
        return
    if new_streak >= _STREAK_THRESHOLD:
        try:
            failures = client.lrange(f"wf:failures:{workflow}", 0, 2) or []
            _send_streak_alert(workflow, int(new_streak), failures)
        except Exception as exc:
            logger.warning(f"streak alert trigger failed: {exc}")


def get_status(workflow: str) -> Optional[dict]:
    """Return the stored state for one workflow, or None if absent/unavailable.
    Return shape: { status, time_iso, summary?, duration_ms?, reason?, streak }"""
    client = _get_client()
    if client is None:
        return None
    try:
        raw = client.get(f"wf:last_run:{workflow}")
        if raw is None:
            return None
        data = json.loads(raw)
        streak_raw = client.get(f"wf:streak:{workflow}")
        data["streak"] = int(streak_raw) if streak_raw is not None else 0
        return data
    except Exception as exc:
        logger.warning(f"state_store.get_status failed: {exc}")
        return None


def get_all_status(workflows: list) -> dict:
    """Return dict mapping each workflow name to its status dict or None."""
    return {wf: get_status(wf) for wf in workflows}


def try_claim_alert_key(key: str, ttl_seconds: int) -> bool:
    """Atomic Redis SET NX EX. Returns True if the caller claimed the key
    (should fire the alert), False if the key already existed (someone else
    alerted). Degrades permissive: returns True on any Redis failure so an
    alert still fires rather than silently swallowed.

    Use for idempotent alert suppression (e.g., watchdog missing-cron
    notifications that must fire exactly once per miss)."""
    client = _get_client()
    if client is None:
        return True
    try:
        result = client.set(key, "1", nx=True, ex=ttl_seconds)
        return result is not None and result is not False
    except Exception as exc:
        logger.warning(f"state_store.try_claim_alert_key failed: {exc}")
        return True


_STREAK_THRESHOLD = 3


def _send_streak_alert(workflow: str, streak: int, failures: list) -> None:
    """Send a distinct Telegram message (not an edit) summarizing the streak.
    Overridable in tests. Never raises."""
    try:
        from execution.integrations.telegram_client import TelegramClient
    except Exception as exc:
        logger.warning(f"_send_streak_alert: telegram import failed: {exc}")
        return
    lines = [f"🚨 ALERTA: {workflow.upper().replace('_', ' ')} falhou {streak}x seguidas", ""]
    if failures:
        lines.append("Ultimas falhas:")
        for f in failures[:3]:
            try:
                entry = json.loads(f) if isinstance(f, str) else f
                t = entry.get("time", "")[:16].replace("T", " ")
                reason = entry.get("reason", "?")
                lines.append(f"• {t} — {reason}")
            except Exception:
                lines.append(f"• {f}")
    dashboard = os.getenv("DASHBOARD_BASE_URL", "https://workflows-minerals.vercel.app")
    lines.append("")
    lines.append(f"[Ver dashboard]({dashboard}/)")
    text = "\n".join(lines)
    try:
        client = TelegramClient()
        client.send_message(text=text, chat_id=os.getenv("TELEGRAM_CHAT_ID"))
    except Exception as exc:
        logger.warning(f"_send_streak_alert: send failed: {exc}")
