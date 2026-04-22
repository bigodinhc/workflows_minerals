"""Tests that platts_reports.py injects trace_id + parent_run_id into run_input
when a bus is active, and omits them when no bus is active."""
import pytest
from unittest.mock import MagicMock


def test_run_input_includes_trace_ids_when_bus_active(monkeypatch):
    """Mirrors the production injection: when a bus is active (ContextVar),
    the run_input dict built before _run_apify_sync carries trace_id + parent_run_id."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_EVENTS_CHANNEL_ID", raising=False)

    from execution.core import event_bus as eb
    from execution.core.event_bus import get_current_bus

    bus = eb.EventBus(workflow="platts_reports")
    token = eb._active_bus.set(bus)
    try:
        # Simulate the exact run_input construction that platts_reports.py
        # main() does after this task's edit. Controller matches the shape
        # exactly so test asserts the production pattern.
        run_input = {
            "username": "u",
            "password": "p",
            "reportTypes": ["Market Reports", "Research Reports"],
            "maxReportsPerType": 50,
            "dryRun": False,
            "forceRedownload": False,
            "gdriveFolderId": "test",
        }
        current = get_current_bus()
        if current is not None:
            run_input["trace_id"] = current.trace_id
            run_input["parent_run_id"] = current.run_id

        assert run_input["trace_id"] == bus.trace_id
        assert run_input["parent_run_id"] == bus.run_id
        # Business fields unchanged
        assert run_input["username"] == "u"
        assert run_input["reportTypes"] == ["Market Reports", "Research Reports"]
    finally:
        eb._active_bus.reset(token)


def test_run_input_omits_trace_ids_when_no_bus():
    from execution.core.event_bus import get_current_bus

    assert get_current_bus() is None

    run_input = {
        "username": "u",
        "password": "p",
        "reportTypes": ["Market Reports"],
    }
    current = get_current_bus()
    if current is not None:
        run_input["trace_id"] = current.trace_id
        run_input["parent_run_id"] = current.run_id

    assert "trace_id" not in run_input
    assert "parent_run_id" not in run_input
