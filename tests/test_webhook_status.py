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

    # Stub heavy optional deps not installed in test environment
    for mod in ("anthropic", "flask"):
        if mod not in sys.modules:
            monkeypatch.setitem(sys.modules, mod, MagicMock())

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

    # Workflow names are rendered with escaped underscores for Telegram Markdown
    assert "✅" in joined and r"morning\_check" in joined and "100/100" in joined
    assert "❌" in joined and r"daily\_report" in joined
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
