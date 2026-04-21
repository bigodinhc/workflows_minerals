"""Tests for execution.core.event_bus module."""
import json
import os
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
