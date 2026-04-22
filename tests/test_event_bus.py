"""Tests for execution.core.event_bus module."""
import json
import os
import sys
import pytest


def test_event_bus_generates_run_id_when_none_provided(monkeypatch):
    # Disable all optional sinks so the test is deterministic
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
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
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
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


class _FakeTelegramClient:
    """Captures send_message / edit_message_text calls with auto-incrementing message_ids."""

    def __init__(self):
        self.sends: list = []
        self.edits: list = []
        self._next_id = 100

    def send_message(self, text, chat_id=None, **kwargs):
        self.sends.append({"text": text, "chat_id": chat_id})
        mid = self._next_id
        self._next_id += 1
        return mid

    def edit_message_text(self, chat_id, message_id, new_text, **kwargs):
        self.edits.append({"text": new_text, "chat_id": chat_id, "message_id": message_id})
        return True


def _wire_events_channel(monkeypatch, fake_client):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_EVENTS_CHANNEL_ID", "-1001234567890")
    from execution.core import event_bus as eb
    monkeypatch.setattr(eb, "_build_telegram_client", lambda: fake_client)
    return eb


def test_events_channel_sink_sends_new_message_on_first_event(monkeypatch):
    """First event of a run → send_message; message_id captured for future edits."""
    fake = _FakeTelegramClient()
    eb = _wire_events_channel(monkeypatch, fake)

    bus = eb.EventBus(workflow="wf_a")
    bus.emit("step", label="first step", level="info")

    assert len(fake.sends) == 1
    assert fake.edits == []
    assert fake.sends[0]["chat_id"] == "-1001234567890"
    # Unknown workflow → fallback title 🛠️ WF A
    assert "WF A" in fake.sends[0]["text"]
    assert "first step" in fake.sends[0]["text"]


def test_events_channel_sink_uses_friendly_title_for_known_workflow(monkeypatch):
    """Known workflow renders with its pretty title at the top of the card."""
    fake = _FakeTelegramClient()
    eb = _wire_events_channel(monkeypatch, fake)

    bus = eb.EventBus(workflow="daily_report")
    bus.emit("step", label="starting", level="info")

    first_line = fake.sends[0]["text"].split("\n")[0]
    assert "Daily SGX" in first_line
    assert "📊" in first_line


def test_events_channel_sink_falls_back_title_when_workflow_unknown(monkeypatch):
    """Unknown workflow → 🛠️ emoji + upper-cased name as title."""
    fake = _FakeTelegramClient()
    eb = _wire_events_channel(monkeypatch, fake)

    bus = eb.EventBus(workflow="some_new_workflow")
    bus.emit("step", label="x", level="info")

    first_line = fake.sends[0]["text"].split("\n")[0]
    assert "🛠️" in first_line
    assert "SOME NEW WORKFLOW" in first_line


def test_events_channel_sink_edits_same_message_on_subsequent_events(monkeypatch):
    """Second+ events edit the message created by the first — one card per run."""
    fake = _FakeTelegramClient()
    eb = _wire_events_channel(monkeypatch, fake)

    bus = eb.EventBus(workflow="wf_b")
    bus.emit("step", label="step one", level="info")
    bus.emit("step", label="step two", level="info")
    bus.emit("step", label="step three", level="info")

    assert len(fake.sends) == 1  # only one send, for the first event
    assert len(fake.edits) == 2   # two edits for events 2 and 3
    # All edits target the same message_id that send_message returned
    expected_mid = 100  # fake client starts at 100
    assert all(e["message_id"] == expected_mid for e in fake.edits)
    # Final edit contains all three labels
    final_text = fake.edits[-1]["text"]
    assert "step one" in final_text
    assert "step two" in final_text
    assert "step three" in final_text


def test_events_channel_sink_renders_past_lines_as_done_and_last_as_in_progress(monkeypatch):
    """After N info events, earlier lines show ✅ and the last shows ⏳.

    Card layout: lines[0]=title, lines[1]=blank, lines[2..] are events."""
    fake = _FakeTelegramClient()
    eb = _wire_events_channel(monkeypatch, fake)

    bus = eb.EventBus(workflow="wf_c")
    bus.emit("step", label="one", level="info")
    bus.emit("step", label="two", level="info")
    bus.emit("step", label="three", level="info")

    final_text = fake.edits[-1]["text"]
    lines = final_text.split("\n")
    # title + blank + 3 events = 5
    assert len(lines) == 5
    assert "✅" in lines[2]  # past
    assert "✅" in lines[3]  # past
    assert "⏳" in lines[4]  # current
    assert "⏳" not in lines[2]
    assert "⏳" not in lines[3]


def test_events_channel_sink_finalizes_on_cron_finished(monkeypatch):
    """cron_finished → last line rendered with ✅, not ⏳."""
    fake = _FakeTelegramClient()
    eb = _wire_events_channel(monkeypatch, fake)

    bus = eb.EventBus(workflow="wf_d")
    bus.emit("step", label="working", level="info")
    bus.emit("cron_finished", level="info")

    final_text = fake.edits[-1]["text"]
    lines = final_text.split("\n")
    # title + blank + step + cron_finished
    assert "✅" in lines[2]              # past step
    assert "✅" in lines[-1]             # cron_finished renders as ✅
    assert "⏳" not in final_text         # no more in-progress
    assert "cron_finished" in final_text


def test_events_channel_sink_finalizes_on_cron_crashed(monkeypatch):
    """cron_crashed → last line rendered with 🚨 regardless of level."""
    fake = _FakeTelegramClient()
    eb = _wire_events_channel(monkeypatch, fake)

    bus = eb.EventBus(workflow="wf_e")
    bus.emit("step", label="working", level="info")
    bus.emit("cron_crashed", label="RuntimeError: boom", level="error")

    final_text = fake.edits[-1]["text"]
    assert "🚨" in final_text
    assert "cron_crashed" in final_text
    assert "RuntimeError" in final_text


def test_events_channel_sink_renders_warn_with_warning_emoji(monkeypatch):
    """A warn-level event (not a terminal lifecycle event) → ⚠️ emoji on that line."""
    fake = _FakeTelegramClient()
    eb = _wire_events_channel(monkeypatch, fake)

    bus = eb.EventBus(workflow="wf_f")
    bus.emit("step", label="ok", level="info")
    bus.emit("api_call", label="uazapi rate-limited", level="warn")

    final_text = fake.edits[-1]["text"]
    lines = final_text.split("\n")
    # title + blank + step + warn
    assert "✅" in lines[2]       # past info is done
    assert "⚠️" in lines[3]      # warn line uses its own emoji, not ⏳


def test_events_channel_sink_sink_exceptions_are_swallowed(monkeypatch):
    """send_message / edit_message_text raising must not break subsequent emits."""
    class FlakyClient:
        def __init__(self):
            self.calls = 0
        def send_message(self, **kwargs):
            self.calls += 1
            raise RuntimeError("telegram down")
        def edit_message_text(self, **kwargs):
            self.calls += 1
            raise RuntimeError("telegram down")

    fake = FlakyClient()
    eb = _wire_events_channel(monkeypatch, fake)

    bus = eb.EventBus(workflow="wf_g")
    # Must not raise
    bus.emit("step", label="one", level="info")
    bus.emit("step", label="two", level="info")
    assert fake.calls == 2  # one send (failed), one edit (failed)


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
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
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
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
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
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
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
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
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
