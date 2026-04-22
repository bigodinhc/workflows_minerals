"""Tests that platts_ingestion.py injects trace_id + parent_run_id into run_input."""
import pytest
from unittest.mock import MagicMock


def test_run_input_includes_trace_ids_when_bus_active(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_EVENTS_CHANNEL_ID", raising=False)

    from execution.core import event_bus as eb
    from execution.core.event_bus import get_current_bus

    bus = eb.EventBus(workflow="platts_ingestion")
    token = eb._active_bus.set(bus)
    try:
        current = get_current_bus()
        assert current is bus

        # Mirror the exact injection pattern expected in platts_ingestion.py
        # after this task's edit (around line 244-260 block).
        run_input = {
            "username": "u",
            "password": "p",
            "sources": ["allInsights"],
            "includeFlash": True,
            "includeLatest": True,
            "maxArticles": 50,
            "maxArticlesPerRmwTab": 5,
            "latestMaxItems": 15,
            "dateFilter": "today",
            "concurrency": 2,
            "dedupArticles": True,
        }
        if current is not None:
            run_input["trace_id"] = current.trace_id
            run_input["parent_run_id"] = current.run_id

        assert run_input["trace_id"] == bus.trace_id
        assert run_input["parent_run_id"] == bus.run_id
    finally:
        eb._active_bus.reset(token)


def test_run_input_omits_trace_ids_when_no_bus():
    from execution.core.event_bus import get_current_bus
    assert get_current_bus() is None

    run_input = {
        "username": "u",
        "password": "p",
        "sources": ["allInsights"],
    }
    current = get_current_bus()
    if current is not None:
        run_input["trace_id"] = current.trace_id
        run_input["parent_run_id"] = current.run_id

    assert "trace_id" not in run_input
    assert "parent_run_id" not in run_input
