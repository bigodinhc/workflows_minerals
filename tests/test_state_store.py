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
