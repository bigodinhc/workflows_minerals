"""Tests for webhook/workflow_trigger.py."""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import json

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

import pytest


@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_fake_token")
    monkeypatch.setenv("GITHUB_OWNER", "bigodinhc")
    monkeypatch.setenv("GITHUB_REPO", "workflows_minerals")


@pytest.fixture
def wf():
    """Fresh import of workflow_trigger module."""
    if "workflow_trigger" in sys.modules:
        del sys.modules["workflow_trigger"]
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


@patch("workflow_trigger.requests.get")
def test_render_workflow_list_success(mock_get, wf):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "workflow_runs": [
            {
                "id": 123,
                "name": "morning_check",
                "path": ".github/workflows/morning_check.yml",
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-04-16T08:30:00Z",
            }
        ]
    }
    mock_get.return_value = mock_response
    text, markup = wf.render_workflow_list()
    assert "Workflows" in text or "workflows" in text.lower()
    assert markup is not None
    buttons = markup["inline_keyboard"]
    assert len(buttons) >= 5
    assert any("wf_run:" in btn["callback_data"] for row in buttons for btn in row)


@patch("workflow_trigger.requests.get")
def test_render_workflow_list_api_failure(mock_get, wf):
    mock_get.side_effect = Exception("Connection timeout")
    text, markup = wf.render_workflow_list()
    assert markup is not None
    buttons = markup["inline_keyboard"]
    assert len(buttons) >= 5


@patch("workflow_trigger.requests.post")
def test_trigger_workflow_success(mock_post, wf):
    mock_response = MagicMock()
    mock_response.status_code = 204
    mock_post.return_value = mock_response
    ok, error = wf.trigger_workflow("morning_check.yml")
    assert ok is True
    assert error is None
    call_url = mock_post.call_args[0][0]
    assert "morning_check.yml" in call_url
    assert "dispatches" in call_url


@patch("workflow_trigger.requests.post")
def test_trigger_workflow_failure(mock_post, wf):
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.text = "Not Found"
    mock_post.return_value = mock_response
    ok, error = wf.trigger_workflow("nonexistent.yml")
    assert ok is False
    assert error is not None


@patch("workflow_trigger.requests.get")
def test_check_run_status_completed(mock_get, wf):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "status": "completed",
        "conclusion": "success",
        "html_url": "https://github.com/bigodinhc/workflows_minerals/actions/runs/999",
    }
    mock_get.return_value = mock_response
    status, conclusion, url = wf.check_run_status(999)
    assert status == "completed"
    assert conclusion == "success"
    assert "999" in url
