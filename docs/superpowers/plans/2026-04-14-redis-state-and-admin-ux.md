# Redis State + Admin UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Redis-backed state store that records every workflow run outcome, triggers a Telegram alert when a workflow fails 3 times in a row, and exposes a `/status` command in the webhook that returns a one-screen snapshot of all 5 workflows.

**Architecture:** A stateless `state_store` module (functions, not a class) wraps the `redis` Python client. `ProgressReporter` calls into it from `finish()`, `finish_empty()`, and a new `fail()` method. Scripts gain an outer `try/except` that invokes `progress.fail(exc)` before re-raising. The two ingestion scripts (market_news, rationale_news) get minimal direct state_store calls since they don't use ProgressReporter. Webhook adds a `/status` handler that reads state_store + parses `.github/workflows/*.yml` cron expressions with `croniter` to show next scheduled runs.

**Tech Stack:** Python 3.9, Redis (Upstash free tier, connected via `REDIS_URL` env var), `redis` client lib, `fakeredis` for tests, `croniter` for cron parsing, pytest.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `execution/core/state_store.py` | Redis client + record_/get_ API + streak alert side-effect | Create |
| `tests/test_state_store.py` | Unit tests using fakeredis | Create |
| `execution/core/progress_reporter.py` | Extend with `fail()` + state_store calls on terminal methods | Modify |
| `tests/test_progress_reporter.py` | Append state_store integration tests | Modify |
| `execution/scripts/morning_check.py` | Add outer try/except → progress.fail | Modify |
| `execution/scripts/send_daily_report.py` | Same pattern | Modify |
| `execution/scripts/baltic_ingestion.py` | Same pattern | Modify |
| `execution/scripts/send_news.py` | Same pattern | Modify |
| `execution/scripts/market_news_ingestion.py` | Direct state_store calls (no ProgressReporter) | Modify |
| `execution/scripts/rationale_ingestion.py` | Direct state_store calls (no ProgressReporter) | Modify |
| `webhook/app.py` | Add `/status` handler + `_parse_next_run` helper | Modify |
| `tests/test_webhook_status.py` | `/status` formatting tests | Create |
| `requirements.txt` | Add redis, croniter, fakeredis | Modify |
| `Dockerfile` | No change needed (requirements.txt picks up new deps) | — |
| `.github/workflows/*.yml` (all 5) | Add REDIS_URL env | Modify |

---

### Task 1: `state_store.record_*` functions (no streak alert yet)

**Files:**
- Create: `execution/core/state_store.py`
- Test: `tests/test_state_store.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Add deps to requirements.txt**

Open `requirements.txt` and append:

```
redis>=5.0,<6.0
croniter>=2.0,<3.0
fakeredis>=2.20,<3.0
```

Install locally:

```
cd "/Users/bigode/Dev/Antigravity WF " && python3 -m pip install "redis>=5.0,<6.0" "croniter>=2.0,<3.0" "fakeredis>=2.20,<3.0"
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_state_store.py`:

```python
"""Tests for execution.core.state_store module."""
import json
import pytest
import fakeredis
from unittest.mock import patch


@pytest.fixture
def fake_redis(monkeypatch):
    """Injects a fakeredis instance as the module-level client."""
    fake = fakeredis.FakeRedis(decode_responses=True)
    from execution.core import state_store
    monkeypatch.setattr(state_store, "_get_client", lambda: fake)
    return fake


def test_record_success_writes_last_run_json(fake_redis):
    from execution.core.state_store import record_success
    record_success("morning_check", summary={"total": 10, "success": 10, "failure": 0}, duration_ms=240000)
    raw = fake_redis.get("wf:last_run:morning_check")
    data = json.loads(raw)
    assert data["status"] == "success"
    assert data["summary"] == {"total": 10, "success": 10, "failure": 0}
    assert data["duration_ms"] == 240000
    assert "time_iso" in data


def test_record_success_deletes_streak(fake_redis):
    from execution.core.state_store import record_success, record_failure
    record_failure("test", summary={"total": 1, "success": 0, "failure": 1}, duration_ms=100)
    record_failure("test", summary={"total": 1, "success": 0, "failure": 1}, duration_ms=100)
    assert fake_redis.get("wf:streak:test") == "2"

    record_success("test", summary={"total": 1, "success": 1, "failure": 0}, duration_ms=100)
    assert fake_redis.get("wf:streak:test") is None


def test_record_failure_increments_streak(fake_redis):
    from execution.core.state_store import record_failure
    record_failure("test", summary={"total": 5, "success": 0, "failure": 5}, duration_ms=100)
    record_failure("test", summary={"total": 5, "success": 0, "failure": 5}, duration_ms=100)
    assert fake_redis.get("wf:streak:test") == "2"


def test_record_failure_pushes_to_failures_list(fake_redis):
    from execution.core.state_store import record_failure
    record_failure("test", summary={"total": 1, "success": 0, "failure": 1}, duration_ms=100)
    record_failure("test", summary={"total": 1, "success": 0, "failure": 1}, duration_ms=100)
    record_failure("test", summary={"total": 1, "success": 0, "failure": 1}, duration_ms=100)
    record_failure("test", summary={"total": 1, "success": 0, "failure": 1}, duration_ms=100)
    # LPUSH + LTRIM 0 2 keeps at most 3
    assert fake_redis.llen("wf:failures:test") == 3


def test_record_empty_does_not_touch_streak(fake_redis):
    from execution.core.state_store import record_failure, record_empty
    record_failure("test", summary={"total": 1, "success": 0, "failure": 1}, duration_ms=100)
    assert fake_redis.get("wf:streak:test") == "1"
    record_empty("test", "no data yet")
    # Streak unchanged
    assert fake_redis.get("wf:streak:test") == "1"
    # Last run updated to empty
    raw = fake_redis.get("wf:last_run:test")
    data = json.loads(raw)
    assert data["status"] == "empty"
    assert data["reason"] == "no data yet"


def test_record_crash_increments_streak(fake_redis):
    from execution.core.state_store import record_crash
    record_crash("test", "LSEG connection timeout")
    record_crash("test", "LSEG connection timeout")
    assert fake_redis.get("wf:streak:test") == "2"
    raw = fake_redis.get("wf:last_run:test")
    data = json.loads(raw)
    assert data["status"] == "crash"
    assert "LSEG" in data["reason"]
```

- [ ] **Step 3: Run tests to confirm they fail**

Run:
```
cd "/Users/bigode/Dev/Antigravity WF " && python3 -m pytest tests/test_state_store.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'execution.core.state_store'`.

- [ ] **Step 4: Create `execution/core/state_store.py`**

```python
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
```

- [ ] **Step 5: Run tests to confirm they pass**

Run:
```
cd "/Users/bigode/Dev/Antigravity WF " && python3 -m pytest tests/test_state_store.py -v
```
Expected: 6 passed.

- [ ] **Step 6: Commit**

```
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/core/state_store.py tests/test_state_store.py requirements.txt && git commit -m "feat: add state_store module with record_success/failure/empty/crash"
```

---

### Task 2: `state_store.get_status` / `get_all_status`

**Files:**
- Modify: `execution/core/state_store.py`
- Test: `tests/test_state_store.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_state_store.py`:

```python
def test_get_status_returns_parsed_dict(fake_redis):
    from execution.core.state_store import record_success, get_status
    record_success("test", summary={"total": 3, "success": 3, "failure": 0}, duration_ms=1000)
    status = get_status("test")
    assert status["status"] == "success"
    assert status["summary"]["total"] == 3
    assert status["streak"] == 0


def test_get_status_includes_streak_for_failures(fake_redis):
    from execution.core.state_store import record_failure, get_status
    record_failure("test", summary={"total": 1, "success": 0, "failure": 1}, duration_ms=100)
    record_failure("test", summary={"total": 1, "success": 0, "failure": 1}, duration_ms=100)
    status = get_status("test")
    assert status["streak"] == 2


def test_get_status_returns_none_for_unknown_workflow(fake_redis):
    from execution.core.state_store import get_status
    assert get_status("nonexistent") is None


def test_get_all_status_returns_dict_keyed_by_workflow(fake_redis):
    from execution.core.state_store import record_success, record_failure, get_all_status
    record_success("a", summary={"total": 1, "success": 1, "failure": 0}, duration_ms=100)
    record_failure("b", summary={"total": 1, "success": 0, "failure": 1}, duration_ms=100)
    result = get_all_status(["a", "b", "c"])
    assert result["a"]["status"] == "success"
    assert result["b"]["status"] == "failure"
    assert result["c"] is None


def test_get_status_when_redis_unavailable_returns_none(monkeypatch):
    from execution.core import state_store
    monkeypatch.setattr(state_store, "_get_client", lambda: None)
    assert state_store.get_status("anything") is None


def test_record_functions_noop_when_redis_unavailable(monkeypatch):
    from execution.core import state_store
    monkeypatch.setattr(state_store, "_get_client", lambda: None)
    # Must not raise
    state_store.record_success("x", {"total": 1, "success": 1, "failure": 0}, 100)
    state_store.record_failure("x", {"total": 1, "success": 0, "failure": 1}, 100)
    state_store.record_empty("x", "reason")
    state_store.record_crash("x", "boom")
```

- [ ] **Step 2: Run tests to confirm they fail**

Run:
```
cd "/Users/bigode/Dev/Antigravity WF " && python3 -m pytest tests/test_state_store.py -v
```
Expected: 6 pass + 6 fail (AttributeError or name not found for `get_status`/`get_all_status`).

- [ ] **Step 3: Add functions to `execution/core/state_store.py`**

Append to the module:

```python
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
```

- [ ] **Step 4: Run tests**

Run:
```
cd "/Users/bigode/Dev/Antigravity WF " && python3 -m pytest tests/test_state_store.py -v
```
Expected: 12 passed.

- [ ] **Step 5: Commit**

```
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/core/state_store.py tests/test_state_store.py && git commit -m "feat: add state_store get_status and get_all_status"
```

---

### Task 3: Streak alert side-effect

**Files:**
- Modify: `execution/core/state_store.py`
- Test: `tests/test_state_store.py`

Goal: when `record_failure` or `record_crash` pushes the streak to >= 3, send a Telegram alert. The alert call is injectable so tests don't need a real Telegram token.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_state_store.py`:

```python
def test_streak_alert_fires_when_streak_reaches_3(fake_redis, monkeypatch):
    from execution.core import state_store
    calls = []
    monkeypatch.setattr(state_store, "_send_streak_alert", lambda wf, streak, failures: calls.append((wf, streak, len(failures))))
    state_store.record_failure("test", {"total": 1, "success": 0, "failure": 1}, 100)
    assert calls == []  # streak=1
    state_store.record_failure("test", {"total": 1, "success": 0, "failure": 1}, 100)
    assert calls == []  # streak=2
    state_store.record_failure("test", {"total": 1, "success": 0, "failure": 1}, 100)
    assert len(calls) == 1
    wf, streak, n_failures = calls[0]
    assert wf == "test"
    assert streak == 3
    assert n_failures == 3


def test_streak_alert_fires_on_crash_too(fake_redis, monkeypatch):
    from execution.core import state_store
    calls = []
    monkeypatch.setattr(state_store, "_send_streak_alert", lambda wf, streak, failures: calls.append(wf))
    state_store.record_crash("test", "err1")
    state_store.record_crash("test", "err2")
    state_store.record_crash("test", "err3")
    assert calls == ["test"]


def test_streak_alert_fires_again_on_4th_and_5th(fake_redis, monkeypatch):
    from execution.core import state_store
    calls = []
    monkeypatch.setattr(state_store, "_send_streak_alert", lambda wf, streak, failures: calls.append(streak))
    for _ in range(5):
        state_store.record_failure("test", {"total": 1, "success": 0, "failure": 1}, 100)
    assert calls == [3, 4, 5]


def test_streak_alert_exception_does_not_propagate(fake_redis, monkeypatch):
    from execution.core import state_store
    def broken_alert(wf, streak, failures):
        raise RuntimeError("telegram down")
    monkeypatch.setattr(state_store, "_send_streak_alert", broken_alert)
    # Must not raise
    state_store.record_failure("test", {"total": 1, "success": 0, "failure": 1}, 100)
    state_store.record_failure("test", {"total": 1, "success": 0, "failure": 1}, 100)
    state_store.record_failure("test", {"total": 1, "success": 0, "failure": 1}, 100)
    # Streak still updated
    assert fake_redis.get("wf:streak:test") == "3"
```

- [ ] **Step 2: Run tests to confirm 4 new failures**

Run:
```
cd "/Users/bigode/Dev/Antigravity WF " && python3 -m pytest tests/test_state_store.py -v
```
Expected: 12 pass + 4 fail (AttributeError: module has no attribute `_send_streak_alert`).

- [ ] **Step 3: Modify `execution/core/state_store.py`**

Add the module-level `_send_streak_alert` function at the end of the file:

```python
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
```

Modify `record_failure` and `record_crash` to check the streak after `INCR` and fire the alert when threshold is reached. Replace the current bodies with:

```python
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


def record_crash(workflow: str, exc_text: str) -> None:
    """Record a workflow crash (uncaught exception). Increments streak. May trigger alert."""
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
```

- [ ] **Step 4: Run tests**

Run:
```
cd "/Users/bigode/Dev/Antigravity WF " && python3 -m pytest tests/test_state_store.py -v
```
Expected: 16 passed.

- [ ] **Step 5: Commit**

```
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/core/state_store.py tests/test_state_store.py && git commit -m "feat: add streak alert side-effect to state_store"
```

---

### Task 4: `ProgressReporter.fail(exception)` method

**Files:**
- Modify: `execution/core/progress_reporter.py`
- Test: `tests/test_progress_reporter.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_progress_reporter.py`:

```python
def test_fail_edits_message_with_crash_marker():
    fake_client = MagicMock()
    fake_client.send_message.return_value = 42
    fake_client.edit_message_text.return_value = True
    reporter = ProgressReporter(
        workflow="morning_check",
        chat_id="chat-1",
        telegram_client=fake_client,
    )
    reporter.start()
    fake_client.reset_mock()

    reporter.fail(RuntimeError("LSEG down"))

    fake_client.edit_message_text.assert_called_once()
    kwargs = fake_client.edit_message_text.call_args.kwargs
    assert "🚨" in kwargs["new_text"]
    assert "CRASH" in kwargs["new_text"]
    assert "LSEG down" in kwargs["new_text"]


def test_fail_records_crash_to_state_store(monkeypatch):
    fake_client = MagicMock()
    fake_client.send_message.return_value = 42
    fake_client.edit_message_text.return_value = True
    calls = []
    import execution.core.progress_reporter as pr_module
    monkeypatch.setattr(
        "execution.core.state_store.record_crash",
        lambda wf, txt: calls.append((wf, txt)),
    )
    reporter = ProgressReporter(
        workflow="morning_check",
        chat_id="chat-1",
        telegram_client=fake_client,
    )
    reporter.start()
    reporter.fail(RuntimeError("boom!"))
    assert len(calls) == 1
    assert calls[0][0] == "morning_check"
    assert "boom!" in calls[0][1]


def test_fail_noop_telegram_when_disabled_but_still_records():
    fake_client = MagicMock()
    fake_client.send_message.return_value = None  # disabled
    calls = []
    from unittest.mock import patch
    reporter = ProgressReporter(
        workflow="test",
        chat_id="chat-1",
        telegram_client=fake_client,
    )
    reporter.start()
    fake_client.reset_mock()
    with patch("execution.core.state_store.record_crash", lambda wf, txt: calls.append(wf)):
        reporter.fail(RuntimeError("x"))
    fake_client.edit_message_text.assert_not_called()
    assert calls == ["test"]


def test_fail_swallows_telegram_exception():
    fake_client = MagicMock()
    fake_client.send_message.return_value = 42
    fake_client.edit_message_text.side_effect = RuntimeError("telegram down")
    reporter = ProgressReporter(
        workflow="test",
        chat_id="chat-1",
        telegram_client=fake_client,
    )
    reporter.start()
    # Must not raise
    reporter.fail(RuntimeError("original"))


def test_fail_swallows_state_store_exception(monkeypatch):
    fake_client = MagicMock()
    fake_client.send_message.return_value = 42
    fake_client.edit_message_text.return_value = True

    def broken_record(wf, txt):
        raise RuntimeError("redis down")
    monkeypatch.setattr("execution.core.state_store.record_crash", broken_record)

    reporter = ProgressReporter(
        workflow="test",
        chat_id="chat-1",
        telegram_client=fake_client,
    )
    reporter.start()
    # Must not raise
    reporter.fail(RuntimeError("original"))
```

- [ ] **Step 2: Run tests to confirm failures**

Run:
```
cd "/Users/bigode/Dev/Antigravity WF " && python3 -m pytest tests/test_progress_reporter.py -v
```
Expected: existing tests pass + 5 fail (AttributeError: no `fail` method).

- [ ] **Step 3: Add `fail()` to ProgressReporter**

Append to the `ProgressReporter` class in `execution/core/progress_reporter.py`:

```python
    def fail(self, exception: Exception) -> None:
        """Edit message with crash marker and record to state store.
        Called from outer try/except in script main(). Never raises."""
        exc_text = str(exception)[:200]
        if not self._disabled and self._message_id is not None:
            text = self._header("🚨", f"CRASH: {exc_text}")
            try:
                client = self._get_client()
                client.edit_message_text(
                    chat_id=self.chat_id,
                    message_id=self._message_id,
                    new_text=text,
                )
            except Exception as e:
                print(f"[WARN] ProgressReporter.fail telegram edit failed: {e}")
        try:
            from execution.core import state_store
            state_store.record_crash(self.workflow, exc_text)
        except Exception as e:
            print(f"[WARN] ProgressReporter.fail state_store failed: {e}")
```

- [ ] **Step 4: Run tests**

Run:
```
cd "/Users/bigode/Dev/Antigravity WF " && python3 -m pytest tests/test_progress_reporter.py -v
```
Expected: all tests pass (existing + 5 new).

- [ ] **Step 5: Commit**

```
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/core/progress_reporter.py tests/test_progress_reporter.py && git commit -m "feat: add ProgressReporter.fail() for crash visibility"
```

---

### Task 5: Wire state_store into `finish` / `finish_empty`

**Files:**
- Modify: `execution/core/progress_reporter.py`
- Test: `tests/test_progress_reporter.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_progress_reporter.py`:

```python
def test_finish_records_success_when_any_delivery_ok(monkeypatch):
    fake_client = MagicMock()
    fake_client.send_message.return_value = 42
    fake_client.edit_message_text.return_value = True
    calls = []
    monkeypatch.setattr(
        "execution.core.state_store.record_success",
        lambda wf, summary, duration_ms: calls.append((wf, summary, duration_ms)),
    )
    monkeypatch.setattr(
        "execution.core.state_store.record_failure",
        lambda wf, summary, duration_ms: calls.append(("FAIL", wf)),
    )
    reporter = ProgressReporter(
        workflow="morning_check",
        chat_id="chat-1",
        telegram_client=fake_client,
    )
    reporter.start()

    results = [DeliveryResult(contact=Contact(name="A", phone="1"), success=True, error=None, duration_ms=0)]
    report = _make_report("morning_check", results)
    reporter.finish(report)

    assert any(c[0] == "morning_check" for c in calls)
    assert not any(isinstance(c, tuple) and c[0] == "FAIL" for c in calls)


def test_finish_records_failure_when_all_deliveries_failed(monkeypatch):
    fake_client = MagicMock()
    fake_client.send_message.return_value = 42
    fake_client.edit_message_text.return_value = True
    calls = []
    monkeypatch.setattr(
        "execution.core.state_store.record_failure",
        lambda wf, summary, duration_ms: calls.append(wf),
    )
    monkeypatch.setattr(
        "execution.core.state_store.record_success",
        lambda wf, summary, duration_ms: calls.append("SHOULD_NOT_HAPPEN"),
    )
    reporter = ProgressReporter(
        workflow="test",
        chat_id="chat-1",
        telegram_client=fake_client,
    )
    reporter.start()

    results = [
        DeliveryResult(contact=Contact(name=f"U{i}", phone=str(i)), success=False, error="boom", duration_ms=0)
        for i in range(3)
    ]
    report = _make_report("test", results)
    reporter.finish(report)
    assert calls == ["test"]


def test_finish_empty_records_to_state_store(monkeypatch):
    fake_client = MagicMock()
    fake_client.send_message.return_value = 42
    fake_client.edit_message_text.return_value = True
    calls = []
    monkeypatch.setattr(
        "execution.core.state_store.record_empty",
        lambda wf, reason: calls.append((wf, reason)),
    )
    reporter = ProgressReporter(
        workflow="test",
        chat_id="chat-1",
        telegram_client=fake_client,
    )
    reporter.start()
    reporter.finish_empty("no data")
    assert calls == [("test", "no data")]


def test_finish_state_store_errors_swallowed(monkeypatch):
    fake_client = MagicMock()
    fake_client.send_message.return_value = 42
    fake_client.edit_message_text.return_value = True

    def broken(*args, **kwargs):
        raise RuntimeError("redis down")
    monkeypatch.setattr("execution.core.state_store.record_success", broken)
    monkeypatch.setattr("execution.core.state_store.record_failure", broken)
    monkeypatch.setattr("execution.core.state_store.record_empty", broken)

    reporter = ProgressReporter(
        workflow="test",
        chat_id="chat-1",
        telegram_client=fake_client,
    )
    reporter.start()

    results = [DeliveryResult(contact=Contact(name="A", phone="1"), success=True, error=None, duration_ms=0)]
    report = _make_report("test", results)
    # Must not raise
    reporter.finish(report)
    reporter.finish_empty("x")
```

- [ ] **Step 2: Run tests to confirm 4 new failures**

Run:
```
cd "/Users/bigode/Dev/Antigravity WF " && python3 -m pytest tests/test_progress_reporter.py -v
```
Expected: the 4 new tests fail (state_store not called yet from finish/finish_empty).

- [ ] **Step 3: Modify `finish()` and `finish_empty()` in `execution/core/progress_reporter.py`**

Find the current `finish()` method. Immediately before the return/exit of the method (after the `edit_message_text` try/except block), add:

```python
        try:
            from execution.core import state_store
            summary = {
                "total": report.total,
                "success": report.success_count,
                "failure": report.failure_count,
            }
            duration_ms = int((report.finished_at - report.started_at).total_seconds() * 1000)
            if report.success_count > 0:
                state_store.record_success(self.workflow, summary, duration_ms)
            else:
                state_store.record_failure(self.workflow, summary, duration_ms)
        except Exception as exc:
            print(f"[WARN] ProgressReporter.finish state_store failed: {exc}")
```

Find the current `finish_empty()` method. After the existing `edit_message_text` try/except, append:

```python
        try:
            from execution.core import state_store
            state_store.record_empty(self.workflow, reason)
        except Exception as exc:
            print(f"[WARN] ProgressReporter.finish_empty state_store failed: {exc}")
```

- [ ] **Step 4: Run tests**

Run:
```
cd "/Users/bigode/Dev/Antigravity WF " && python3 -m pytest tests/test_progress_reporter.py -v
```
Expected: all tests pass.

- [ ] **Step 5: Commit**

```
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/core/progress_reporter.py tests/test_progress_reporter.py && git commit -m "feat: record workflow outcome to state_store in finish/finish_empty"
```

---

### Task 6: Outer try/except in `morning_check.py`, `send_daily_report.py`, `baltic_ingestion.py`, `send_news.py`

**Files:**
- Modify: `execution/scripts/morning_check.py`
- Modify: `execution/scripts/send_daily_report.py`
- Modify: `execution/scripts/baltic_ingestion.py`
- Modify: `execution/scripts/send_news.py`

For each of the 4 scripts, the pattern is identical:

- [ ] **Step 1: Read the file to locate the `def main():` body**

Run:
```
cd "/Users/bigode/Dev/Antigravity WF " && grep -n "^def main\|^if __name__" execution/scripts/morning_check.py
```

Repeat for the other 3 scripts.

- [ ] **Step 2: Wrap the post-`progress.start()` body of each `main()` in try/except**

All four scripts already create and call `progress.start(...)` early in `main()` (before any early-exit paths). The change is minimal: put `try:` right after `progress.start(...)` and put the entire remaining body inside. On `except Exception as exc`, call `progress.fail(exc)` then re-raise.

Concrete pattern to apply to each script:

```python
def main():
    # (existing: argparse, logger init, etc., unchanged)

    progress = ProgressReporter(
        workflow="morning_check",  # or the appropriate label
        chat_id=os.getenv("TELEGRAM_CHAT_ID"),
        gh_run_id=os.getenv("GITHUB_RUN_ID"),
    )
    progress.start("Preparando dados...")

    try:
        # (existing body from here to end of main — unchanged:
        #  check_daily_status, fetch data, build message, dispatch, finish, etc.)
        ...
    except Exception as exc:
        progress.fail(exc)
        raise
```

Do NOT change any existing logic inside the body — no added/removed/moved lines. The try/except is purely an outer wrapper.

**Edge case:** if any script has code between `progress = ProgressReporter(...)` and `progress.start(...)` that can raise, the `progress.fail()` call will still work because `fail()` is defined to handle the case where `start()` was never called (it checks `self._message_id is not None`). But none of the four scripts currently have such code, so no special handling is needed.

- [ ] **Step 3: Run full test suite after each edit**

After modifying each script:
```
cd "/Users/bigode/Dev/Antigravity WF " && python3 -m pytest -v
```
Expected: all tests still pass. Import-check the script:
```
cd "/Users/bigode/Dev/Antigravity WF " && python3 -c "import execution.scripts.morning_check"
```
(Skip the import check for `baltic_ingestion` — it requires `msal` which isn't installed locally.)

- [ ] **Step 4: Commit once all 4 scripts are done**

```
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/scripts/morning_check.py execution/scripts/send_daily_report.py execution/scripts/baltic_ingestion.py execution/scripts/send_news.py && git commit -m "feat: wrap workflow scripts with crash handler calling progress.fail"
```

---

### Task 7: Direct state_store calls in `market_news_ingestion.py` and `rationale_ingestion.py`

**Files:**
- Modify: `execution/scripts/market_news_ingestion.py`
- Modify: `execution/scripts/rationale_ingestion.py`

These scripts do not use `ProgressReporter` (they send an approval-request Telegram message, not a broadcast). Wire state_store directly so `/status` can show their outcome.

- [ ] **Step 1: Read each script and locate exit points**

Run:
```
cd "/Users/bigode/Dev/Antigravity WF " && grep -n "^def main\|sys.exit\|return" execution/scripts/market_news_ingestion.py
cd "/Users/bigode/Dev/Antigravity WF " && grep -n "^def main\|sys.exit\|return" execution/scripts/rationale_ingestion.py
```

- [ ] **Step 2: Add import at top of each script**

Near the other `execution.*` imports in each file:

```python
from execution.core import state_store
```

Also define the workflow label constant near the top (below imports, above function definitions):

For `market_news_ingestion.py`:
```python
WORKFLOW_NAME = "market_news"
```

For `rationale_ingestion.py`:
```python
WORKFLOW_NAME = "rationale_news"
```

- [ ] **Step 3: Wrap `main()` in try/except and add state_store calls**

For both scripts:

1. Wrap the entire `main()` body in a `try:` block. In the `except Exception as exc:`, call:
```python
        state_store.record_crash(WORKFLOW_NAME, str(exc)[:200])
        raise
```

2. At the top of `main()` (after argparse), if `--dry-run` is set and the script exits early, just `return` (no state_store call — don't pollute real state with dry-run data).

3. Before every other non-success `return` or `sys.exit(0)` in main, add:
```python
    state_store.record_empty(WORKFLOW_NAME, "<short reason>")
```
   Reason strings to use per guard:
   - No new articles: `"sem noticias novas"`
   - Empty/failed scrape: `"scrape vazio ou falhou"`
   - Failed to send approval request: `"falha ao enviar approval request"`

4. At the very end of successful execution (after approval request is sent successfully), before returning, add:
```python
    summary = {"total": 1, "success": 1, "failure": 0}  # 1 approval request sent
    state_store.record_success(WORKFLOW_NAME, summary, 0)
```

Pick `duration_ms=0` since these scripts don't track duration today. Acceptable trade-off — the value only affects `/status` display precision.

- [ ] **Step 4: Verify tests still pass**

Run:
```
cd "/Users/bigode/Dev/Antigravity WF " && python3 -m pytest -v
```
Expected: all tests still pass.

Smoke-import:
```
cd "/Users/bigode/Dev/Antigravity WF " && python3 -c "
import execution.scripts.market_news_ingestion
import execution.scripts.rationale_ingestion
print('ok')
"
```
Expected: `ok`.

- [ ] **Step 5: Commit**

```
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/scripts/market_news_ingestion.py execution/scripts/rationale_ingestion.py && git commit -m "feat: record news ingestion outcome to state_store"
```

---

### Task 8: `_parse_next_run` helper for cron → next datetime in BRT

**Files:**
- Create: `execution/core/cron_parser.py`
- Test: `tests/test_cron_parser.py`

Keeping this out of `webhook/app.py` so it's testable and reusable.

- [ ] **Step 1: Write failing tests**

Create `tests/test_cron_parser.py`:

```python
"""Tests for execution.core.cron_parser module."""
import os
import tempfile
from datetime import datetime, timezone
from execution.core.cron_parser import parse_next_run


def _write_yaml(path, content):
    with open(path, "w") as f:
        f.write(content)


def test_parse_next_run_returns_brt_datetime_from_single_cron(tmp_path, monkeypatch):
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir()
    yml = wf_dir / "morning_check.yml"
    yml.write_text("""name: Morning
on:
  schedule:
    - cron: '30 11 * * 1-5'
""")
    # Anchor "now" to a known instant for determinism
    monkeypatch.setattr(
        "execution.core.cron_parser._utc_now",
        lambda: datetime(2026, 4, 14, 10, 0, 0, tzinfo=timezone.utc),
    )
    result = parse_next_run("morning_check", workflows_dir=str(wf_dir))
    # 11:30 UTC on a weekday is 08:30 BRT
    assert result is not None
    assert result.hour == 8
    assert result.minute == 30


def test_parse_next_run_returns_earliest_when_multiple_crons(tmp_path, monkeypatch):
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir()
    yml = wf_dir / "daily_report.yml"
    yml.write_text("""name: Daily
on:
  schedule:
    - cron: '0 12 * * *'
    - cron: '0 15 * * *'
    - cron: '0 18 * * *'
""")
    monkeypatch.setattr(
        "execution.core.cron_parser._utc_now",
        lambda: datetime(2026, 4, 14, 13, 0, 0, tzinfo=timezone.utc),
    )
    result = parse_next_run("daily_report", workflows_dir=str(wf_dir))
    # Next after 13:00 UTC is 15:00 UTC = 12:00 BRT
    assert result.hour == 12
    assert result.minute == 0


def test_parse_next_run_returns_none_when_yaml_missing(tmp_path):
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir()
    result = parse_next_run("nonexistent", workflows_dir=str(wf_dir))
    assert result is None


def test_parse_next_run_returns_none_when_no_schedule(tmp_path):
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir()
    yml = wf_dir / "manual.yml"
    yml.write_text("""name: Manual
on:
  workflow_dispatch:
""")
    result = parse_next_run("manual", workflows_dir=str(wf_dir))
    assert result is None


def test_parse_next_run_returns_none_when_yaml_malformed(tmp_path):
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir()
    yml = wf_dir / "broken.yml"
    yml.write_text("not: [valid yaml")
    result = parse_next_run("broken", workflows_dir=str(wf_dir))
    assert result is None
```

- [ ] **Step 2: Run tests to confirm failure**

Run:
```
cd "/Users/bigode/Dev/Antigravity WF " && python3 -m pytest tests/test_cron_parser.py -v
```
Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 3: Create `execution/core/cron_parser.py`**

```python
"""
Parse GitHub Actions workflow YAML files to compute the next scheduled run
in BRT (America/Sao_Paulo). Used by the webhook /status command.

Never raises: returns None on any parse/IO/schedule failure.
"""
import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

_BRT = timezone(timedelta(hours=-3))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_next_run(workflow: str, workflows_dir: str = ".github/workflows") -> Optional[datetime]:
    """Return the next scheduled run of `workflow` in BRT, or None.
    `workflow` is the base filename (without .yml) in `workflows_dir`."""
    path = os.path.join(workflows_dir, f"{workflow}.yml")
    if not os.path.exists(path):
        return None
    try:
        import yaml
    except Exception as exc:
        logger.warning(f"cron_parser: pyyaml not installed: {exc}")
        return None
    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f)
    except Exception as exc:
        logger.warning(f"cron_parser: failed to parse {path}: {exc}")
        return None
    if not isinstance(data, dict):
        return None
    # GH Actions accepts `on:` as dict or as string; schedule only makes sense in dict form.
    # PyYAML parses `on:` as boolean True in some configurations; handle both.
    on_section = data.get("on") or data.get(True)
    if not isinstance(on_section, dict):
        return None
    schedule = on_section.get("schedule")
    if not isinstance(schedule, list) or not schedule:
        return None
    try:
        from croniter import croniter
    except Exception as exc:
        logger.warning(f"cron_parser: croniter not installed: {exc}")
        return None
    now_utc = _utc_now()
    next_runs = []
    for entry in schedule:
        cron_expr = entry.get("cron") if isinstance(entry, dict) else None
        if not cron_expr:
            continue
        try:
            it = croniter(cron_expr, now_utc)
            next_runs.append(it.get_next(datetime))
        except Exception as exc:
            logger.warning(f"cron_parser: bad cron {cron_expr!r}: {exc}")
            continue
    if not next_runs:
        return None
    earliest = min(next_runs)
    # croniter returns naive datetime in the given base's tz; we passed UTC-aware
    if earliest.tzinfo is None:
        earliest = earliest.replace(tzinfo=timezone.utc)
    return earliest.astimezone(_BRT)
```

- [ ] **Step 4: Add `pyyaml` to requirements.txt if not already present**

Check:
```
cd "/Users/bigode/Dev/Antigravity WF " && grep -i "pyyaml\|^yaml" requirements.txt
```

If not present, append to `requirements.txt`:
```
pyyaml>=6.0,<7.0
```

Install:
```
cd "/Users/bigode/Dev/Antigravity WF " && python3 -m pip install "pyyaml>=6.0,<7.0"
```

- [ ] **Step 5: Run tests**

Run:
```
cd "/Users/bigode/Dev/Antigravity WF " && python3 -m pytest tests/test_cron_parser.py -v
```
Expected: 5 passed.

- [ ] **Step 6: Commit**

```
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/core/cron_parser.py tests/test_cron_parser.py requirements.txt && git commit -m "feat: add cron_parser for next-scheduled-run lookup"
```

---

### Task 9: `/status` command handler in webhook

**Files:**
- Modify: `webhook/app.py`
- Test: `tests/test_webhook_status.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_webhook_status.py`:

```python
"""Tests for the /status command in webhook/app.py."""
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

# Add webhook dir to sys.path so we can import app
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

import pytest


@pytest.fixture
def app_module(monkeypatch):
    """Import the webhook app module with minimal env setup."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111")
    # Ensure clean import
    if "app" in sys.modules:
        del sys.modules["app"]
    import app as app_module
    return app_module


def test_format_status_lines_handles_all_states(app_module):
    """Unit test the formatter without invoking the full webhook."""
    brt = timezone(timedelta(hours=-3))
    states = {
        "morning_check": {
            "status": "success",
            "time_iso": datetime(2026, 4, 14, 8, 30, tzinfo=brt).isoformat(),
            "summary": {"total": 100, "success": 100, "failure": 0},
            "duration_ms": 240000,
            "streak": 0,
        },
        "daily_report": {
            "status": "failure",
            "time_iso": datetime(2026, 4, 14, 9, 0, tzinfo=brt).isoformat(),
            "summary": {"total": 100, "success": 0, "failure": 100},
            "duration_ms": 30000,
            "streak": 1,
        },
        "baltic_ingestion": None,
        "market_news": {
            "status": "empty",
            "time_iso": datetime(2026, 4, 14, 6, 0, tzinfo=brt).isoformat(),
            "reason": "sem noticias novas",
            "streak": 0,
        },
        "rationale_news": {
            "status": "crash",
            "time_iso": datetime(2026, 4, 14, 7, 0, tzinfo=brt).isoformat(),
            "reason": "LSEG timeout",
            "streak": 3,
        },
    }
    next_runs = {
        "morning_check": None,
        "daily_report": None,
        "baltic_ingestion": datetime(2026, 4, 14, 16, 0, tzinfo=brt),
        "market_news": None,
        "rationale_news": None,
    }

    lines = app_module._format_status_lines(states, next_runs)
    joined = "\n".join(lines)

    assert "✅" in joined and "morning_check" in joined and "100/100" in joined
    assert "❌" in joined and "daily_report" in joined
    assert "⏳ proximo" in joined and "16:00" in joined
    assert "ℹ️" in joined and "sem noticias novas" in joined
    assert "🚨" in joined and "3 falhas seguidas" in joined


def test_format_status_handles_all_none(app_module):
    """When Redis is offline all states are None."""
    states = {wf: None for wf in ["a", "b"]}
    next_runs = {wf: None for wf in ["a", "b"]}
    lines = app_module._format_status_lines(states, next_runs)
    joined = "\n".join(lines)
    assert "⏳" in joined
```

- [ ] **Step 2: Run tests to confirm failure**

Run:
```
cd "/Users/bigode/Dev/Antigravity WF " && python3 -m pytest tests/test_webhook_status.py -v
```
Expected: FAIL with `AttributeError: module 'app' has no attribute '_format_status_lines'`.

- [ ] **Step 3: Add helper + handler to `webhook/app.py`**

Near the top (below imports, above route definitions), add:

```python
ALL_WORKFLOWS = [
    "morning_check",
    "daily_report",
    "baltic_ingestion",
    "market_news",
    "rationale_news",
]


def _format_status_lines(states: dict, next_runs: dict) -> list:
    """Build per-workflow lines for the /status response."""
    max_name = max(len(w) for w in states.keys()) if states else 0
    lines = []
    for workflow, st in states.items():
        label = f"{workflow}:".ljust(max_name + 2)
        if st is not None and st.get("streak", 0) >= 3:
            lines.append(f"{label} 🚨 {st['streak']} falhas seguidas")
            continue
        if st is None:
            nxt = next_runs.get(workflow)
            when = nxt.strftime("%H:%M") if nxt else "?"
            lines.append(f"{label} ⏳ proximo {when} BRT")
            continue
        status = st.get("status")
        time_iso = st.get("time_iso", "")
        try:
            hhmm = time_iso[11:16]
        except Exception:
            hhmm = "??:??"
        if status == "success":
            summary = st.get("summary", {})
            ok = summary.get("success", 0)
            total = summary.get("total", 0)
            dur_ms = st.get("duration_ms", 0)
            dur = f"{dur_ms // 60000}m" if dur_ms >= 60000 else f"{dur_ms // 1000}s"
            lines.append(f"{label} ✅ {hhmm} BRT ({ok}/{total}, {dur})")
        elif status == "failure":
            summary = st.get("summary", {})
            total = summary.get("total", 0)
            lines.append(f"{label} ❌ {hhmm} BRT (0/{total} enviadas)")
        elif status == "crash":
            reason = (st.get("reason") or "")[:40]
            lines.append(f"{label} 💥 {hhmm} BRT (crash: {reason})")
        elif status == "empty":
            reason = st.get("reason", "")
            lines.append(f"{label} ℹ️ {hhmm} BRT ({reason})")
        else:
            lines.append(f"{label} ? estado desconhecido")
    return lines


def _build_status_message() -> str:
    """Fetch state + cron + format full /status body."""
    from execution.core import state_store, cron_parser
    from datetime import datetime, timezone, timedelta
    brt = timezone(timedelta(hours=-3))
    states = state_store.get_all_status(ALL_WORKFLOWS)
    if all(v is None for v in states.values()):
        # Probe if Redis itself is dead (not just "never recorded")
        if state_store._get_client() is None:
            return "⚠️ Store de estado indisponivel. Abra o dashboard pra ver historico."
    next_runs = {wf: cron_parser.parse_next_run(wf) for wf in ALL_WORKFLOWS}
    header = datetime.now(brt).strftime("📊 STATUS (%d/%m %H:%M BRT)")
    lines = _format_status_lines(states, next_runs)
    dashboard_url = os.getenv("DASHBOARD_BASE_URL", "https://workflows-minerals.vercel.app")
    return header + "\n\n" + "\n".join(lines) + f"\n\n[Dashboard]({dashboard_url}/)"
```

Then find the block in the `/webhook` handler that processes slash commands (around line 1069 where `if text.startswith("/"):` appears). Add a new branch for `/status` alongside the existing `/add`, `/list` handlers:

```python
        if text == "/status":
            if not contact_admin.is_authorized(chat_id):
                logger.warning(f"/status rejected: chat_id={chat_id} not authorized")
                return jsonify({"ok": True})
            try:
                body = _build_status_message()
            except Exception as exc:
                logger.error(f"/status failed: {exc}")
                body = f"⚠️ Erro ao gerar status: {str(exc)[:100]}"
            send_telegram_message(chat_id, body)
            return jsonify({"ok": True})
```

Also update the `/start` help text so the admin discovers the new command. Find the `if text == "/start":` block and add `"`/status` — status dos workflows\n"` to the help list.

- [ ] **Step 4: Run tests**

Run:
```
cd "/Users/bigode/Dev/Antigravity WF " && python3 -m pytest tests/test_webhook_status.py -v
```
Expected: 2 passed.

Run full suite too:
```
cd "/Users/bigode/Dev/Antigravity WF " && python3 -m pytest -v
```
Expected: all tests pass.

- [ ] **Step 5: Commit**

```
cd "/Users/bigode/Dev/Antigravity WF " && git add webhook/app.py tests/test_webhook_status.py && git commit -m "feat: add /status command with workflow snapshot"
```

---

### Task 10: Wire `REDIS_URL` into GitHub Actions workflows

**Files:**
- Modify: `.github/workflows/morning_check.yml`
- Modify: `.github/workflows/daily_report.yml`
- Modify: `.github/workflows/baltic_ingestion.yml`
- Modify: `.github/workflows/market_news.yml`
- Modify: `.github/workflows/rationale_news.yml`

- [ ] **Step 1: Find the env block of each workflow**

Run:
```
cd "/Users/bigode/Dev/Antigravity WF " && grep -n "TELEGRAM_BOT_TOKEN" .github/workflows/*.yml
```

Each workflow has a step with `env:` listing secret env vars. `REDIS_URL` goes there.

- [ ] **Step 2: Add `REDIS_URL` alongside existing TELEGRAM env vars**

For each of the 5 yml files, find the env block(s) that include `TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}` and add on the next line:

```yaml
          REDIS_URL: ${{ secrets.REDIS_URL }}
```

(Matching indentation — usually 10 spaces.)

- [ ] **Step 3: Commit**

```
cd "/Users/bigode/Dev/Antigravity WF " && git add .github/workflows/*.yml && git commit -m "ci: inject REDIS_URL into all scheduled workflows"
```

- [ ] **Step 4: (Manual, pre-deploy)** — create the secret

Document for the operator: before pushing, add `REDIS_URL` and `DASHBOARD_BASE_URL` as GitHub repository secrets, and set `REDIS_URL` in Railway dashboard for the webhook service. Until those are set, state_store is a silent no-op (existing behavior is preserved).

---

### Task 11: Final verification

- [ ] **Step 1: Full test suite**

Run:
```
cd "/Users/bigode/Dev/Antigravity WF " && python3 -m pytest -v
```
Expected: all tests pass. Count should increase by roughly: 6 (task 1) + 6 (task 2) + 4 (task 3) + 5 (task 4) + 4 (task 5) + 5 (task 8) + 2 (task 9) = 32 new tests on top of the 107 that existed before this plan.

- [ ] **Step 2: Smoke-import every modified script**

Run:
```
cd "/Users/bigode/Dev/Antigravity WF " && python3 -c "
import execution.core.state_store
import execution.core.cron_parser
import execution.core.progress_reporter
import execution.scripts.morning_check
import execution.scripts.send_daily_report
import execution.scripts.send_news
import execution.scripts.market_news_ingestion
import execution.scripts.rationale_ingestion
print('ok')
"
```
Expected: `ok`.

(Baltic script requires `msal`, skipped.)

- [ ] **Step 3: Manual end-to-end (requires Redis)**

If REDIS_URL is set locally:

```
cd "/Users/bigode/Dev/Antigravity WF " && TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... REDIS_URL=... python3 execution/scripts/morning_check.py --dry-run
```

Then query Redis directly (e.g., `redis-cli GET wf:last_run:morning_check`) to confirm state was written. This step is optional and only to be done if the engineer has local Redis / Upstash access.

- [ ] **Step 4: Push to origin/main**

```
cd "/Users/bigode/Dev/Antigravity WF " && git push origin main
```
