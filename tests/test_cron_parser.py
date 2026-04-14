"""Tests for execution.core.cron_parser module."""
import os
import tempfile
from datetime import datetime, timezone
from execution.core.cron_parser import parse_next_run


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
