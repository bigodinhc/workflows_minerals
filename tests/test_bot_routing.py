"""Tests for workflow → delivery destination routing."""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

import pytest


def test_client_workflows_route_to_channel():
    from bot.routing import resolve_destination, DEST_CLIENT_CHANNEL
    for wf in (
        "daily_report", "market_news", "platts_reports",
        "morning_check", "baltic_ingestion",
    ):
        assert resolve_destination(wf) == DEST_CLIENT_CHANNEL


def test_unknown_and_none_route_to_internal():
    from bot.routing import resolve_destination, DEST_INTERNAL
    assert resolve_destination("something_new") == DEST_INTERNAL
    assert resolve_destination(None) == DEST_INTERNAL


def test_delivery_mode_defaults_to_telegram(monkeypatch):
    monkeypatch.delenv("CLIENT_DELIVERY_CHANNEL", raising=False)
    from bot.routing import client_delivery_mode
    assert client_delivery_mode() == "telegram"


def test_delivery_mode_uazapi(monkeypatch):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "uazapi")
    from bot.routing import client_delivery_mode
    assert client_delivery_mode() == "uazapi"


def test_delivery_mode_garbage_falls_back_to_telegram(monkeypatch):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "smoke-signals")
    from bot.routing import client_delivery_mode
    assert client_delivery_mode() == "telegram"
