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


def test_emit_continues_when_one_sink_raises(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://fake")
    monkeypatch.setenv("SUPABASE_KEY", "fake")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    class ExplodingSupabaseClient:
        def table(self, name):
            raise RuntimeError("supabase is down")

    from execution.core import event_bus as eb
    monkeypatch.setattr(eb, "_get_supabase_client", lambda: ExplodingSupabaseClient())

    bus = eb.EventBus(workflow="wf", run_id="rX")
    bus.emit("cron_started")  # must not raise
    # Stdout sink (runs before Supabase) still fires
    out = capsys.readouterr().out
    assert "cron_started" in out
    assert "rX" in out


def test_with_event_bus_emits_started_and_finished(monkeypatch, capsys):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    from execution.core.event_bus import with_event_bus

    calls = []

    @with_event_bus("test_wf")
    def main():
        calls.append("inside main")
        return "ok"

    result = main()
    assert result == "ok"
    assert calls == ["inside main"]

    out_lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    events = [e["event"] for e in out_lines]
    assert events == ["cron_started", "cron_finished"]
    assert all(e["workflow"] == "test_wf" for e in out_lines)
    # Both lifecycle events share the same run_id
    assert out_lines[0]["run_id"] == out_lines[1]["run_id"]


def test_with_event_bus_catches_exception_and_re_raises(monkeypatch, capsys):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    captures = []
    fake_sentry = type(sys)("sentry_sdk")
    fake_sentry.add_breadcrumb = lambda **kw: None
    fake_sentry.capture_exception = lambda exc: captures.append(str(exc)[:50])

    import sys as _sys
    monkeypatch.setitem(_sys.modules, "sentry_sdk", fake_sentry)

    from execution.core.event_bus import with_event_bus

    @with_event_bus("test_wf")
    def broken_main():
        raise ValueError("synthetic boom")

    with pytest.raises(ValueError, match="synthetic boom"):
        broken_main()

    out_lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    events = [e["event"] for e in out_lines]
    assert events == ["cron_started", "cron_crashed"]
    crashed = out_lines[1]
    assert crashed["level"] == "error"
    assert "ValueError" in (crashed["label"] or "")
    assert "synthetic boom" in (crashed["label"] or "")

    # Sentry captured the exception
    assert len(captures) == 1
    assert "synthetic boom" in captures[0]


def test_with_event_bus_calls_init_sentry(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    calls = []
    from execution.core import sentry_init as si_module
    monkeypatch.setattr(si_module, "init_sentry", lambda name: calls.append(name) or True)

    from execution.core.event_bus import with_event_bus

    @with_event_bus("baltic_ingestion")
    def main():
        return None

    main()
    # init_sentry called once with the workflow-derived script name
    assert len(calls) == 1
    assert "baltic_ingestion" in calls[0]


def test_with_event_bus_records_crash_to_state_store(monkeypatch, capsys):
    """When the wrapped function raises, the decorator should also update
    state_store.record_crash(workflow, exc_text) so the watchdog knows the
    run was attempted (even if the script failed before progress.fail ran).
    Closes the gap between event_bus and state_store tracking for
    'rodou = tentou rodar' semantics."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    crash_calls = []
    from execution.core import state_store
    monkeypatch.setattr(state_store, "record_crash", lambda wf, exc_text: crash_calls.append((wf, exc_text)))

    from execution.core.event_bus import with_event_bus

    @with_event_bus("test_wf")
    def broken_main():
        raise ValueError("synthetic boom")

    with pytest.raises(ValueError, match="synthetic boom"):
        broken_main()

    assert len(crash_calls) == 1
    wf, exc_text = crash_calls[0]
    assert wf == "test_wf"
    assert "ValueError" in exc_text
    assert "synthetic boom" in exc_text


def test_with_event_bus_record_crash_exception_does_not_propagate(monkeypatch):
    """If state_store.record_crash itself raises, the decorator should still
    re-raise the ORIGINAL exception (not the record_crash one)."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    from execution.core import state_store
    def broken_record(wf, exc_text):
        raise RuntimeError("state_store unavailable")
    monkeypatch.setattr(state_store, "record_crash", broken_record)

    from execution.core.event_bus import with_event_bus

    @with_event_bus("test_wf")
    def broken_main():
        raise ValueError("original")

    # Must still raise the ORIGINAL ValueError, not the RuntimeError from record_crash
    with pytest.raises(ValueError, match="original"):
        broken_main()


def test_events_channel_sink_sends_warn_immediately(monkeypatch):
    """warn/error events flush immediately, no buffering."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_EVENTS_CHANNEL_ID", "-1001234567890")

    sent_messages = []

    class FakeTelegramClient:
        def send_message(self, text, chat_id=None, **kwargs):
            sent_messages.append({"text": text, "chat_id": chat_id})
            return 1

    from execution.core import event_bus as eb
    monkeypatch.setattr(eb, "_build_telegram_client", lambda: FakeTelegramClient())

    bus = eb.EventBus(workflow="wf_a")
    bus.emit("step", label="warning thing", level="warn")

    assert len(sent_messages) == 1
    assert sent_messages[0]["chat_id"] == "-1001234567890"
    assert "wf_a" in sent_messages[0]["text"]
    assert "step" in sent_messages[0]["text"]


def test_events_channel_sink_buffers_info_until_threshold(monkeypatch):
    """info events accumulate until the 20-event threshold or 1s window."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_EVENTS_CHANNEL_ID", "-1001234567890")

    sent_messages = []

    class FakeTelegramClient:
        def send_message(self, text, chat_id=None, **kwargs):
            sent_messages.append({"text": text, "chat_id": chat_id})
            return 1

    from execution.core import event_bus as eb
    monkeypatch.setattr(eb, "_build_telegram_client", lambda: FakeTelegramClient())

    # Freeze time so the batch window doesn't expire
    fake_time = [1000.0]
    monkeypatch.setattr(eb, "_monotonic", lambda: fake_time[0])

    bus = eb.EventBus(workflow="wf_b")
    # Emit 5 info events — under threshold; should NOT have flushed yet
    for i in range(5):
        bus.emit("step", label=f"step_{i}", level="info")

    assert sent_messages == []

    # Emit 15 more — now at 20; should flush exactly once
    for i in range(5, 20):
        bus.emit("step", label=f"step_{i}", level="info")

    assert len(sent_messages) == 1
    assert "step_0" in sent_messages[0]["text"]
    assert "step_19" in sent_messages[0]["text"]


def test_events_channel_sink_flushes_on_time_window(monkeypatch):
    """info events flush when 1s elapses since last flush, even below threshold."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_EVENTS_CHANNEL_ID", "-1001234567890")

    sent_messages = []

    class FakeTelegramClient:
        def send_message(self, text, chat_id=None, **kwargs):
            sent_messages.append({"text": text, "chat_id": chat_id})
            return 1

    from execution.core import event_bus as eb
    monkeypatch.setattr(eb, "_build_telegram_client", lambda: FakeTelegramClient())

    fake_time = [1000.0]
    monkeypatch.setattr(eb, "_monotonic", lambda: fake_time[0])

    bus = eb.EventBus(workflow="wf_c")
    bus.emit("step", label="first", level="info")
    assert sent_messages == []

    # Advance time past the 1s window
    fake_time[0] += 1.5
    bus.emit("step", label="second", level="info")

    # Now the emit should have triggered a flush of the pending buffer
    assert len(sent_messages) == 1
    assert "first" in sent_messages[0]["text"]
    assert "second" in sent_messages[0]["text"]


def test_events_channel_sink_flushes_pending_info_before_warn(monkeypatch):
    """A warn event flushes any buffered info FIRST to preserve ordering."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_EVENTS_CHANNEL_ID", "-1001234567890")

    sent_messages = []

    class FakeTelegramClient:
        def send_message(self, text, chat_id=None, **kwargs):
            sent_messages.append({"text": text, "chat_id": chat_id})
            return 1

    from execution.core import event_bus as eb
    monkeypatch.setattr(eb, "_build_telegram_client", lambda: FakeTelegramClient())

    fake_time = [1000.0]
    monkeypatch.setattr(eb, "_monotonic", lambda: fake_time[0])

    bus = eb.EventBus(workflow="wf_d")
    bus.emit("step", label="buffered_info", level="info")
    bus.emit("step", label="now_warning", level="warn")

    # Expect 2 sends: first the buffered info (flushed), then the warn (immediate)
    assert len(sent_messages) == 2
    assert "buffered_info" in sent_messages[0]["text"]
    assert "now_warning" in sent_messages[1]["text"]


def test_events_channel_sink_disabled_when_env_missing(monkeypatch, capsys):
    """When TELEGRAM_EVENTS_CHANNEL_ID is absent, the sink is not added to _sinks."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_EVENTS_CHANNEL_ID", raising=False)

    from execution.core.event_bus import EventBus

    bus = EventBus(workflow="wf_e")
    bus.emit("step", label="nobody hears", level="info")

    # Only stdout fires
    out = capsys.readouterr().out
    assert "step" in out  # stdout still gets it


def test_get_current_bus_returns_none_outside_decorator():
    from execution.core.event_bus import get_current_bus
    assert get_current_bus() is None


def test_get_current_bus_returns_active_bus_inside_decorator(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_EVENTS_CHANNEL_ID", raising=False)
    from execution.core.event_bus import with_event_bus, get_current_bus
    seen = {}

    @with_event_bus("test_wf")
    def fake_main():
        seen["bus"] = get_current_bus()

    fake_main()
    assert seen["bus"] is not None
    assert seen["bus"].workflow == "test_wf"


def test_get_current_bus_resets_after_decorator_exits(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_EVENTS_CHANNEL_ID", raising=False)
    from execution.core.event_bus import with_event_bus, get_current_bus

    @with_event_bus("test_wf")
    def fake_main():
        assert get_current_bus() is not None

    fake_main()
    assert get_current_bus() is None


def test_get_current_bus_resets_even_when_decorator_raises(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_EVENTS_CHANNEL_ID", raising=False)
    from execution.core.event_bus import with_event_bus, get_current_bus

    @with_event_bus("test_wf")
    def boom():
        raise RuntimeError("oops")

    with pytest.raises(RuntimeError):
        boom()
    assert get_current_bus() is None


def test_get_current_bus_isolated_across_nested_calls(monkeypatch):
    """If a decorated function calls another decorated function, the inner
    bus is active during inner call; outer bus restored afterward."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_EVENTS_CHANNEL_ID", raising=False)
    from execution.core.event_bus import with_event_bus, get_current_bus
    trail = []

    @with_event_bus("inner")
    def inner():
        trail.append(("inner", get_current_bus().workflow))

    @with_event_bus("outer")
    def outer():
        trail.append(("outer-before", get_current_bus().workflow))
        inner()
        trail.append(("outer-after", get_current_bus().workflow))

    outer()
    assert trail == [
        ("outer-before", "outer"),
        ("inner", "inner"),
        ("outer-after", "outer"),
    ]
