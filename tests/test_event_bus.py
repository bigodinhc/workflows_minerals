"""Tests for execution.core.event_bus module."""
import json
import os
import sys
import pytest


def test_event_bus_generates_run_id_when_none_provided(monkeypatch):
    # Disable all optional sinks so the test is deterministic
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TRACE_ID", raising=False)
    monkeypatch.delenv("PARENT_RUN_ID", raising=False)

    from execution.core.event_bus import EventBus

    bus = EventBus(workflow="test_wf")
    assert bus.workflow == "test_wf"
    assert bus.run_id is not None and len(bus.run_id) >= 6
    assert bus.trace_id == bus.run_id  # defaults to run_id when no TRACE_ID env
    assert bus.parent_run_id is None


def test_event_bus_respects_provided_run_id_and_trace_id(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    from execution.core.event_bus import EventBus

    bus = EventBus(workflow="t", run_id="run_abc", trace_id="trace_xyz", parent_run_id="parent_q")
    assert bus.run_id == "run_abc"
    assert bus.trace_id == "trace_xyz"
    assert bus.parent_run_id == "parent_q"


def test_event_bus_inherits_trace_id_from_env(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TRACE_ID", "inherited_trace_42")

    from execution.core.event_bus import EventBus

    bus = EventBus(workflow="t")
    assert bus.trace_id == "inherited_trace_42"
    assert bus.run_id != "inherited_trace_42"  # run_id still auto-generated


def test_stdout_sink_writes_json_line_per_emit(monkeypatch, capsys):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    from execution.core.event_bus import EventBus

    bus = EventBus(workflow="wf_x", run_id="r1", trace_id="t1")
    bus.emit("cron_started")
    bus.emit("step", label="doing thing", detail={"n": 5}, level="info")
    bus.emit("cron_crashed", level="error")

    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 3
    for line in out:
        parsed = json.loads(line)
        assert parsed["workflow"] == "wf_x"
        assert parsed["run_id"] == "r1"
        assert parsed["trace_id"] == "t1"
        assert parsed["event"] in ("cron_started", "step", "cron_crashed")
    assert json.loads(out[1])["label"] == "doing thing"
    assert json.loads(out[1])["detail"] == {"n": 5}
    assert json.loads(out[2])["level"] == "error"


def test_emit_coerces_invalid_level_to_info(monkeypatch, capsys):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    from execution.core.event_bus import EventBus

    bus = EventBus(workflow="t")
    bus.emit("step", level="debug")  # not in {info, warn, error}

    out = capsys.readouterr().out.strip()
    assert json.loads(out)["level"] == "info"


def test_supabase_sink_inserts_row_when_enabled(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "fake_key")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    inserted_rows = []

    class FakeSupabaseClient:
        def table(self, name):
            assert name == "event_log"
            return self

        def insert(self, row):
            inserted_rows.append(row)
            return self

        def execute(self):
            return None

    from execution.core import event_bus as eb

    monkeypatch.setattr(eb, "_get_supabase_client", lambda: FakeSupabaseClient())

    bus = eb.EventBus(workflow="wf_y", run_id="r2")
    bus.emit("cron_started")

    assert len(inserted_rows) == 1
    row = inserted_rows[0]
    assert row["workflow"] == "wf_y"
    assert row["run_id"] == "r2"
    assert row["event"] == "cron_started"
    assert row["level"] == "info"
    # The stdout payload includes 'ts' as ISO; Supabase row either uses its own default
    # or passes through. Either way, the 'ts' key presence is OK.


def test_supabase_sink_disabled_when_env_missing(monkeypatch, capsys):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    from execution.core.event_bus import EventBus

    bus = EventBus(workflow="t")
    bus.emit("cron_started")  # should not raise, should not try Supabase

    # Verify stdout still fires
    assert "cron_started" in capsys.readouterr().out


def test_sentry_sink_adds_breadcrumb_per_emit(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    breadcrumbs_added = []

    fake_sentry = type(sys)("sentry_sdk")
    fake_sentry.add_breadcrumb = lambda **kwargs: breadcrumbs_added.append(kwargs)
    fake_sentry.capture_exception = lambda exc: None

    import sys as _sys
    monkeypatch.setitem(_sys.modules, "sentry_sdk", fake_sentry)

    from execution.core.event_bus import EventBus

    bus = EventBus(workflow="wf_z")
    bus.emit("step", label="working", detail={"n": 1}, level="info")
    bus.emit("cron_crashed", label="BOOM", level="error")

    assert len(breadcrumbs_added) == 2
    first = breadcrumbs_added[0]
    assert first["category"] == "wf_z"
    assert first["level"] == "info"
    assert first["message"] == "working"
    assert first["data"] == {"n": 1}

    second = breadcrumbs_added[1]
    assert second["level"] == "error"
    assert second["message"] == "BOOM"


def test_sentry_sink_graceful_when_sdk_missing(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    # Force sentry_sdk import to fail
    import sys as _sys
    monkeypatch.setitem(_sys.modules, "sentry_sdk", None)

    from execution.core.event_bus import EventBus

    bus = EventBus(workflow="t")
    bus.emit("step", label="no-sentry")  # must not raise


def test_main_chat_sink_sends_on_error(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    sent_messages = []

    class FakeTelegramClient:
        def send_message(self, text, chat_id=None, **kwargs):
            sent_messages.append({"text": text, "chat_id": chat_id})
            return 999

    from execution.core import event_bus as eb
    monkeypatch.setattr(eb, "_build_telegram_client", lambda: FakeTelegramClient())

    bus = eb.EventBus(workflow="morning_check")
    bus.emit("cron_crashed", label="TypeError: boom", level="error")

    assert len(sent_messages) == 1
    msg = sent_messages[0]
    assert msg["chat_id"] == "12345"
    assert "morning_check" in msg["text"].lower() or "MORNING CHECK" in msg["text"]
    assert "CRASH" in msg["text"] or "crash" in msg["text"].lower()
    assert "TypeError: boom" in msg["text"]


def test_main_chat_sink_skips_info_events(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    sent_messages = []

    class FakeTelegramClient:
        def send_message(self, text, chat_id=None, **kwargs):
            sent_messages.append({"text": text, "chat_id": chat_id})
            return 1

    from execution.core import event_bus as eb
    monkeypatch.setattr(eb, "_build_telegram_client", lambda: FakeTelegramClient())

    bus = eb.EventBus(workflow="t")
    bus.emit("step", label="doing thing", level="info")
    bus.emit("cron_started")  # default level info

    assert sent_messages == []


def test_main_chat_sink_disabled_when_env_missing(monkeypatch, capsys):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    from execution.core.event_bus import EventBus

    bus = EventBus(workflow="t")
    bus.emit("cron_crashed", level="error")  # would want to alert, but env missing

    # Stdout still fires
    assert "cron_crashed" in capsys.readouterr().out
