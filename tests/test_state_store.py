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
