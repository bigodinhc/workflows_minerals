"""Tests for throttle, Ref token, and 429 backoff in DeliveryReporter."""
from __future__ import annotations
import re
from unittest.mock import MagicMock, patch
import pytest
from execution.core.delivery_reporter import Contact, DeliveryReporter


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """Replace time.sleep so tests don't actually wait."""
    monkeypatch.setattr("execution.core.delivery_reporter.time.sleep", lambda _: None)


def test_dispatch_appends_ref_token_to_each_message(monkeypatch):
    monkeypatch.setenv("BROADCAST_REF_TOKEN_ENABLED", "true")
    sent = []
    def send_fn(phone, text):
        sent.append((phone, text))

    reporter = DeliveryReporter(workflow="t", send_fn=send_fn, notify_telegram=False)
    contacts = [Contact(name=f"U{i}", phone=f"55{i:03}") for i in range(3)]
    reporter.dispatch(contacts, message="hello world")

    assert len(sent) == 3
    for _phone, text in sent:
        assert text.startswith("hello world\n\nRef: ")
        # 6-char alphanumeric token
        m = re.search(r"Ref: ([A-Za-z0-9_-]{6})$", text)
        assert m is not None, f"no Ref token in: {text!r}"

    tokens = [re.search(r"Ref: (\S+)$", t).group(1) for _p, t in sent]
    assert len(set(tokens)) == 3, "tokens must differ across contacts"


def test_dispatch_omits_ref_token_when_disabled(monkeypatch):
    monkeypatch.setenv("BROADCAST_REF_TOKEN_ENABLED", "false")
    sent = []
    def send_fn(phone, text):
        sent.append(text)

    reporter = DeliveryReporter(workflow="t", send_fn=send_fn, notify_telegram=False)
    contacts = [Contact(name="A", phone="111")]
    reporter.dispatch(contacts, message="hello")

    assert sent == ["hello"]


def test_dispatch_sleeps_between_sends(monkeypatch):
    monkeypatch.setenv("BROADCAST_DELAY_MIN", "15.0")
    monkeypatch.setenv("BROADCAST_DELAY_MAX", "30.0")
    sleeps: list[float] = []
    monkeypatch.setattr(
        "execution.core.delivery_reporter.time.sleep",
        lambda s: sleeps.append(s),
    )
    monkeypatch.setattr(
        "execution.core.delivery_reporter.random.uniform",
        lambda lo, hi: (lo + hi) / 2,
    )

    reporter = DeliveryReporter(
        workflow="t", send_fn=MagicMock(), notify_telegram=False
    )
    contacts = [Contact(name=f"U{i}", phone=f"55{i:03}") for i in range(3)]
    reporter.dispatch(contacts, message="hi")

    # 3 contacts → 2 inter-message sleeps (none after the last)
    assert sleeps == [22.5, 22.5]


def test_dispatch_no_sleep_after_last_contact(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(
        "execution.core.delivery_reporter.time.sleep",
        lambda s: sleeps.append(s),
    )
    monkeypatch.setattr(
        "execution.core.delivery_reporter.random.uniform", lambda lo, hi: 1.0
    )
    reporter = DeliveryReporter(
        workflow="t", send_fn=MagicMock(), notify_telegram=False
    )
    reporter.dispatch([Contact(name="solo", phone="999")], message="hi")
    assert sleeps == []  # single contact, no inter-message sleep


def test_dispatch_no_sleep_for_circuit_broken_skipped(monkeypatch):
    """Circuit-broken contacts don't actually call uazapi → no throttle delay.

    Stronger than just count-based: also verifies which iterations slept,
    so a regression that fires the throttle inside the early-continue path
    would still fail this test.
    """
    sleeps: list[float] = []
    sleep_at_call: list[int] = []
    call_count = {"n": 0}

    def fake_send(phone, text):
        call_count["n"] += 1
        import requests
        resp = MagicMock()
        resp.status_code = 401
        resp.text = '{"message": "auth failed"}'
        raise requests.HTTPError(response=resp)

    def fake_sleep(s):
        sleep_at_call.append(call_count["n"])
        sleeps.append(s)

    monkeypatch.setattr(
        "execution.core.delivery_reporter.time.sleep", fake_sleep
    )
    monkeypatch.setattr(
        "execution.core.delivery_reporter.random.uniform", lambda lo, hi: 5.0
    )

    reporter = DeliveryReporter(
        workflow="t",
        send_fn=fake_send,
        notify_telegram=False,
        circuit_breaker_threshold=2,
    )
    contacts = [Contact(name=f"U{i}", phone=f"55{i:03}") for i in range(5)]
    reporter.dispatch(contacts, message="hi")

    # Sleeps fire AFTER attempts 1 and 2, before circuit trips on iteration 3.
    # If the throttle ever fires on a circuit-broken iteration, sleep_at_call
    # would include 2 (the call count never advances on skipped iterations).
    assert sleeps == [5.0, 5.0]
    assert sleep_at_call == [1, 2]


def test_broadcast_delay_range_rejects_inf_and_nan(monkeypatch):
    from execution.core.delivery_reporter import _broadcast_delay_range
    monkeypatch.setenv("BROADCAST_DELAY_MIN", "15.0")
    monkeypatch.setenv("BROADCAST_DELAY_MAX", "inf")
    lo, hi = _broadcast_delay_range()
    assert lo == 15.0 and hi == 30.0  # falls back to defaults

    monkeypatch.setenv("BROADCAST_DELAY_MAX", "nan")
    lo, hi = _broadcast_delay_range()
    assert lo == 15.0 and hi == 30.0


def test_broadcast_delay_range_rejects_non_numeric(monkeypatch):
    from execution.core.delivery_reporter import _broadcast_delay_range
    monkeypatch.setenv("BROADCAST_DELAY_MIN", "abc")
    monkeypatch.setenv("BROADCAST_DELAY_MAX", "30.0")
    lo, hi = _broadcast_delay_range()
    assert lo == 15.0 and hi == 30.0  # defaults on parse failure


def test_broadcast_delay_range_clamps_huge_max(monkeypatch):
    from execution.core.delivery_reporter import _broadcast_delay_range
    monkeypatch.setenv("BROADCAST_DELAY_MIN", "15.0")
    monkeypatch.setenv("BROADCAST_DELAY_MAX", "9999.0")
    lo, hi = _broadcast_delay_range()
    assert lo == 15.0 and hi == 300.0  # clamped to 300s ceiling


def test_dispatch_extra_sleep_on_rate_limit(monkeypatch):
    monkeypatch.setenv("BROADCAST_RATE_LIMIT_SLEEP", "60.0")
    monkeypatch.setenv("BROADCAST_DELAY_MIN", "15.0")
    monkeypatch.setenv("BROADCAST_DELAY_MAX", "30.0")
    sleeps: list[float] = []
    monkeypatch.setattr(
        "execution.core.delivery_reporter.time.sleep",
        lambda s: sleeps.append(s),
    )
    monkeypatch.setattr(
        "execution.core.delivery_reporter.random.uniform",
        lambda lo, hi: 20.0,
    )

    import requests
    call = {"n": 0}
    def send_fn(phone, text):
        call["n"] += 1
        if call["n"] == 1:
            resp = MagicMock()
            resp.status_code = 429
            resp.text = '{"message": "rate limit exceeded"}'
            raise requests.HTTPError(response=resp)
        # subsequent calls succeed

    reporter = DeliveryReporter(
        workflow="t", send_fn=send_fn, notify_telegram=False
    )
    contacts = [Contact(name=f"U{i}", phone=f"55{i:03}") for i in range(3)]
    reporter.dispatch(contacts, message="hi")

    # Expected sleeps: 60 (rate-limit backoff after attempt 1)
    #                + 20 (regular jitter after attempt 1)
    #                + 20 (regular jitter after attempt 2)
    # No sleep after attempt 3 (last).
    assert sleeps == [60.0, 20.0, 20.0]


def test_delivery_summary_event_includes_throttle_metadata(monkeypatch):
    monkeypatch.setenv("BROADCAST_DELAY_MIN", "15.0")
    monkeypatch.setenv("BROADCAST_DELAY_MAX", "30.0")
    monkeypatch.setattr(
        "execution.core.delivery_reporter.random.uniform", lambda lo, hi: 1.0
    )

    captured = {}
    fake_bus = MagicMock()
    def emit(event, label=None, detail=None, level="info"):
        captured["event"] = event
        captured["detail"] = detail
        captured["level"] = level
    fake_bus.emit.side_effect = emit

    monkeypatch.setattr(
        "execution.core.event_bus.get_current_bus", lambda: fake_bus
    )

    reporter = DeliveryReporter(
        workflow="t", send_fn=MagicMock(), notify_telegram=False
    )
    contacts = [Contact(name=f"U{i}", phone=f"55{i:03}") for i in range(2)]
    reporter.dispatch(contacts, message="hi")

    assert captured["event"] == "delivery_summary"
    detail = captured["detail"]
    assert detail["delay_min"] == 15.0
    assert detail["delay_max"] == 30.0
    assert detail["total"] == 2
    assert "duration_seconds" in detail
    assert isinstance(detail["duration_seconds"], int)
