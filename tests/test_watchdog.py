"""Integration tests for execution.scripts.watchdog."""
import json
import pytest
from datetime import datetime, timezone, timedelta


def _iso(dt):
    return dt.isoformat()


@pytest.fixture
def disable_sinks(monkeypatch):
    """Disable Supabase / Telegram sinks so tests don't hit network."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)


def test_watchdog_emits_cron_missed_when_last_run_too_old(monkeypatch, capsys, disable_sinks):
    """Workflow's previous expected run was 30 minutes ago; grace is 15;
    state store shows last run was yesterday. Expect cron_missed emitted."""
    from execution.scripts import watchdog as wd

    now = datetime(2026, 4, 21, 14, 0, 0, tzinfo=timezone.utc)
    previous_expected = now - timedelta(minutes=30)
    yesterday = now - timedelta(days=1)

    # Patch time
    monkeypatch.setattr(wd, "_utc_now", lambda: now)

    # Patch cron_parser to say 'morning_check' has a previous_expected 30 min ago
    from execution.core import cron_parser
    monkeypatch.setattr(
        cron_parser,
        "parse_previous_run",
        lambda wf, **kwargs: previous_expected if wf == "morning_check" else None,
    )

    # Patch state_store to say morning_check's last run was yesterday
    from execution.core import state_store
    monkeypatch.setattr(
        state_store,
        "get_status",
        lambda wf: {"time_iso": _iso(yesterday), "status": "success"} if wf == "morning_check" else None,
    )
    # Alert claim: succeed (first time alerting)
    claim_calls = []
    def fake_claim(key, ttl_seconds):
        claim_calls.append((key, ttl_seconds))
        return True
    monkeypatch.setattr(state_store, "try_claim_alert_key", fake_claim)

    # Patch ALL_WORKFLOWS to just 'morning_check'
    from webhook import status_builder
    monkeypatch.setattr(status_builder, "ALL_WORKFLOWS", ["morning_check"])

    wd.main()

    # Parse stdout JSON lines to find cron_missed
    out_lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    missed_events = [e for e in out_lines if e["event"] == "cron_missed"]
    assert len(missed_events) == 1
    m = missed_events[0]
    assert m["level"] == "error"
    assert "morning_check" in (m["label"] or "")
    assert m["detail"]["expected_iso"] == _iso(previous_expected)
    assert m["detail"]["last_run_iso"] == _iso(yesterday)

    # Alert claim was attempted with the right key shape
    assert len(claim_calls) == 1
    key, ttl = claim_calls[0]
    assert "morning_check" in key
    assert _iso(previous_expected) in key
    assert ttl == 86400  # 24h


def test_watchdog_skips_when_within_grace_window(monkeypatch, capsys, disable_sinks):
    """Previous expected was 5 minutes ago; grace is 15; still in window → no alert."""
    from execution.scripts import watchdog as wd
    from execution.core import cron_parser, state_store
    from webhook import status_builder

    now = datetime(2026, 4, 21, 14, 0, 0, tzinfo=timezone.utc)
    previous_expected = now - timedelta(minutes=5)  # within 15min grace

    monkeypatch.setattr(wd, "_utc_now", lambda: now)
    monkeypatch.setattr(cron_parser, "parse_previous_run", lambda wf, **kw: previous_expected)
    monkeypatch.setattr(state_store, "get_status", lambda wf: None)  # never ran
    monkeypatch.setattr(state_store, "try_claim_alert_key", lambda k, t: pytest.fail("should not have tried to claim"))
    monkeypatch.setattr(status_builder, "ALL_WORKFLOWS", ["morning_check"])

    wd.main()

    out_lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    missed = [e for e in out_lines if e["event"] == "cron_missed"]
    assert missed == []


def test_watchdog_skips_when_last_run_after_previous_expected(monkeypatch, capsys, disable_sinks):
    """Previous expected 30 min ago; but last_run 10 min ago (i.e., ran late but ran).
    Should NOT alert."""
    from execution.scripts import watchdog as wd
    from execution.core import cron_parser, state_store
    from webhook import status_builder

    now = datetime(2026, 4, 21, 14, 0, 0, tzinfo=timezone.utc)
    previous_expected = now - timedelta(minutes=30)
    last_run = now - timedelta(minutes=10)  # after previous_expected

    monkeypatch.setattr(wd, "_utc_now", lambda: now)
    monkeypatch.setattr(cron_parser, "parse_previous_run", lambda wf, **kw: previous_expected)
    monkeypatch.setattr(state_store, "get_status", lambda wf: {"time_iso": _iso(last_run)})
    monkeypatch.setattr(state_store, "try_claim_alert_key", lambda k, t: pytest.fail("should not claim"))
    monkeypatch.setattr(status_builder, "ALL_WORKFLOWS", ["morning_check"])

    wd.main()

    out_lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert [e for e in out_lines if e["event"] == "cron_missed"] == []


def test_watchdog_is_idempotent_via_claim_guard(monkeypatch, capsys, disable_sinks):
    """When try_claim_alert_key returns False (already alerted), watchdog
    should NOT emit a duplicate cron_missed."""
    from execution.scripts import watchdog as wd
    from execution.core import cron_parser, state_store
    from webhook import status_builder

    now = datetime(2026, 4, 21, 14, 0, 0, tzinfo=timezone.utc)
    previous_expected = now - timedelta(minutes=30)

    monkeypatch.setattr(wd, "_utc_now", lambda: now)
    monkeypatch.setattr(cron_parser, "parse_previous_run", lambda wf, **kw: previous_expected)
    monkeypatch.setattr(state_store, "get_status", lambda wf: None)
    monkeypatch.setattr(state_store, "try_claim_alert_key", lambda k, t: False)  # already claimed
    monkeypatch.setattr(status_builder, "ALL_WORKFLOWS", ["morning_check"])

    wd.main()

    out_lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert [e for e in out_lines if e["event"] == "cron_missed"] == []


def test_watchdog_skips_workflow_with_no_previous_run(monkeypatch, capsys, disable_sinks):
    """If cron_parser.parse_previous_run returns None (workflow has no schedule
    or YAML is missing), skip silently — not an error."""
    from execution.scripts import watchdog as wd
    from execution.core import cron_parser, state_store
    from webhook import status_builder

    now = datetime(2026, 4, 21, 14, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(wd, "_utc_now", lambda: now)
    monkeypatch.setattr(cron_parser, "parse_previous_run", lambda wf, **kw: None)
    monkeypatch.setattr(state_store, "get_status", lambda wf: None)
    monkeypatch.setattr(state_store, "try_claim_alert_key", lambda k, t: pytest.fail("should not claim"))
    monkeypatch.setattr(status_builder, "ALL_WORKFLOWS", ["rationale_news"])  # no YAML

    wd.main()

    out_lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert [e for e in out_lines if e["event"] == "cron_missed"] == []
