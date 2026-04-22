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


def test_parse_previous_run_returns_most_recent_past_occurrence(tmp_path, monkeypatch):
    """parse_previous_run walks one cron interval backward from `now` and
    returns the most recent scheduled run that has already passed."""
    from execution.core import cron_parser

    # Write a minimal workflow YAML: runs at 09:00 UTC Mon-Fri
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir()
    wf_file = wf_dir / "test_wf.yml"
    wf_file.write_text(
        "name: Test\n"
        "on:\n"
        "  schedule:\n"
        "    - cron: '0 9 * * 1-5'\n"
        "jobs:\n"
        "  x:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps: [{run: 'echo'}]\n"
    )

    # Fix `now` to a known Wednesday at 14:00 UTC. The previous 09:00 UTC run
    # happened 5 hours ago, same day.
    from datetime import datetime, timezone
    fixed_now = datetime(2026, 4, 15, 14, 0, 0, tzinfo=timezone.utc)  # Wed
    monkeypatch.setattr(cron_parser, "_utc_now", lambda: fixed_now)

    previous = cron_parser.parse_previous_run("test_wf", workflows_dir=str(wf_dir))

    assert previous is not None
    # parse_previous_run returns in UTC (not BRT) to match `now` semantics used by watchdog
    assert previous.tzinfo is not None
    # The previous 09:00 UTC Wed run is 5 hours before fixed_now
    from datetime import timedelta
    assert previous == fixed_now - timedelta(hours=5)


def test_parse_previous_run_returns_none_when_workflow_missing(tmp_path):
    """If the workflow YAML doesn't exist, return None (not raise)."""
    from execution.core import cron_parser
    previous = cron_parser.parse_previous_run("nonexistent", workflows_dir=str(tmp_path))
    assert previous is None
