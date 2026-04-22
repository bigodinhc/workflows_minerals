"""Tests for execution.core.state_store module."""
import json
import pytest
import fakeredis
from unittest.mock import patch


@pytest.fixture
def fake_redis(monkeypatch):
    """Injects a fakeredis instance as the module-level client.

    Also stubs _send_streak_alert so tests that cross the streak threshold
    never leak to a real Telegram chat. This guard prevents the pollution
    that happened on 2026-04-21: a full pytest run with TELEGRAM_BOT_TOKEN
    + TELEGRAM_CHAT_ID in env fired real "TEST falhou 3x/4x seguidas" alerts
    to the operator's main chat.

    Individual tests that assert alert behavior (test_streak_alert_fires_*)
    override this stub with their own monkeypatch — the fixture stub is the
    default, per-test overrides still work."""
    fake = fakeredis.FakeRedis(decode_responses=True)
    from execution.core import state_store
    monkeypatch.setattr(state_store, "_get_client", lambda: fake)
    monkeypatch.setattr(state_store, "_send_streak_alert", lambda *a, **k: None)
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
    # Clear dedup key to simulate the 5-min TTL having expired between runs.
    # In production, two independent crashes from the same workflow are typically
    # minutes/hours apart; the dedup window only suppresses double-reports of the
    # SAME exception from progress.fail + @with_event_bus.
    fake_redis.delete("wf:crash_dedup:test")
    record_crash("test", "LSEG connection timeout")
    assert fake_redis.get("wf:streak:test") == "2"
    raw = fake_redis.get("wf:last_run:test")
    data = json.loads(raw)
    assert data["status"] == "crash"
    assert "LSEG" in data["reason"]


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
    fake_redis.delete("wf:crash_dedup:test")  # simulate dedup window expiry
    state_store.record_crash("test", "err2")
    fake_redis.delete("wf:crash_dedup:test")
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


def test_try_claim_alert_key_returns_true_on_first_claim(monkeypatch):
    """First caller with a fresh key should return True (alert should fire).
    Uses a fake redis client to avoid real Redis."""
    from execution.core import state_store

    set_calls = []

    class FakeRedis:
        def set(self, key, value, nx=False, ex=None):
            set_calls.append({"key": key, "value": value, "nx": nx, "ex": ex})
            return True  # redis-py returns True/None for SET NX; True = key set

    monkeypatch.setattr(state_store, "_get_client", lambda: FakeRedis())

    assert state_store.try_claim_alert_key("wf:test:1", ttl_seconds=60) is True
    assert len(set_calls) == 1
    assert set_calls[0]["key"] == "wf:test:1"
    assert set_calls[0]["nx"] is True
    assert set_calls[0]["ex"] == 60


def test_try_claim_alert_key_returns_false_on_duplicate_claim(monkeypatch):
    """Second caller with a still-alive key should return False (alert already sent)."""
    from execution.core import state_store

    class FakeRedis:
        def set(self, key, value, nx=False, ex=None):
            return None  # redis-py returns None when NX fails (key already exists)

    monkeypatch.setattr(state_store, "_get_client", lambda: FakeRedis())

    assert state_store.try_claim_alert_key("wf:test:2", ttl_seconds=60) is False


def test_try_claim_alert_key_returns_true_when_redis_unavailable(monkeypatch):
    """When Redis is down, degrade permissive: return True so the alert
    still fires. Losing one duplicate alert is worse than losing the alert
    entirely."""
    from execution.core import state_store
    monkeypatch.setattr(state_store, "_get_client", lambda: None)

    assert state_store.try_claim_alert_key("wf:test:3", ttl_seconds=60) is True


def test_try_claim_alert_key_returns_true_when_redis_raises(monkeypatch):
    """If the Redis SET itself raises (connection reset mid-call), degrade permissive."""
    from execution.core import state_store

    class FlakyRedis:
        def set(self, key, value, nx=False, ex=None):
            raise RuntimeError("connection lost")

    monkeypatch.setattr(state_store, "_get_client", lambda: FlakyRedis())

    assert state_store.try_claim_alert_key("wf:test:4", ttl_seconds=60) is True


def test_record_crash_dedups_within_window(fake_redis):
    """Two record_crash calls for the same workflow within the dedup window
    should only increment the streak ONCE. Models the real-world scenario
    where both progress.fail() and @with_event_bus observe the same exception."""
    from execution.core.state_store import record_crash
    record_crash("some_wf", "ValueError: boom")
    record_crash("some_wf", "ValueError: boom")  # same crash observed from decorator
    assert fake_redis.get("wf:streak:some_wf") == "1"  # not "2"
    # The dedup key is present (SET NX held it)
    assert fake_redis.get("wf:crash_dedup:some_wf") == "1"


def test_record_crash_dedup_is_per_workflow(fake_redis):
    """Different workflows have independent dedup keys — simultaneous crashes
    across workflows should each be recorded."""
    from execution.core.state_store import record_crash
    record_crash("wf_a", "err a")
    record_crash("wf_b", "err b")
    assert fake_redis.get("wf:streak:wf_a") == "1"
    assert fake_redis.get("wf:streak:wf_b") == "1"


def test_record_crash_dedup_records_after_expiry(fake_redis):
    """After the dedup window expires (simulated by deleting the key), a new
    crash IS recorded — streak increments normally."""
    from execution.core.state_store import record_crash
    record_crash("some_wf", "first")
    fake_redis.delete("wf:crash_dedup:some_wf")  # simulate 5-min TTL having passed
    record_crash("some_wf", "second")
    assert fake_redis.get("wf:streak:some_wf") == "2"
