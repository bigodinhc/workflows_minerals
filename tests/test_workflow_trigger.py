"""Tests for webhook/workflow_trigger.py (async version)."""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

import pytest


@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_fake_token")
    monkeypatch.setenv("GITHUB_OWNER", "bigodinhc")
    monkeypatch.setenv("GITHUB_REPO", "workflows_minerals")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake:token")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")


@pytest.fixture
def wf():
    """Fresh import of workflow_trigger module."""
    # Clear cached modules to pick up env vars
    for mod in list(sys.modules):
        if mod.startswith(("workflow_trigger", "bot.")):
            del sys.modules[mod]
    import workflow_trigger
    return workflow_trigger


def test_catalog_has_5_workflows(wf):
    assert len(wf.WORKFLOW_CATALOG) == 5
    ids = [w["id"] for w in wf.WORKFLOW_CATALOG]
    assert "morning_check.yml" in ids
    assert "daily_report.yml" in ids
    assert "baltic_ingestion.yml" in ids
    assert "market_news.yml" in ids
    assert "platts_reports.yml" in ids


def test_catalog_entries_have_required_fields(wf):
    for w in wf.WORKFLOW_CATALOG:
        assert "id" in w
        assert "name" in w
        assert "description" in w


def test_workflow_name_by_id(wf):
    assert wf._workflow_name_by_id("morning_check.yml") == "MORNING CHECK"
    assert wf._workflow_name_by_id("nonexistent") == "nonexistent"


def test_gh_headers(wf):
    headers = wf._gh_headers()
    assert "Authorization" in headers
    assert headers["Authorization"] == "Bearer ghp_fake_token"
