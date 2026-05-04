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
    """Circuit-broken contacts don't actually call uazapi → no throttle delay."""
    sleeps: list[float] = []
    monkeypatch.setattr(
        "execution.core.delivery_reporter.time.sleep",
        lambda s: sleeps.append(s),
    )
    monkeypatch.setattr(
        "execution.core.delivery_reporter.random.uniform", lambda lo, hi: 5.0
    )
    # send_fn that raises a fatal-category error on every call → circuit trips
    import requests
    def send_fn(phone, text):
        resp = MagicMock()
        resp.status_code = 401
        resp.text = '{"message": "auth failed"}'
        err = requests.HTTPError(response=resp)
        raise err

    reporter = DeliveryReporter(
        workflow="t",
        send_fn=send_fn,
        notify_telegram=False,
        circuit_breaker_threshold=2,
    )
    contacts = [Contact(name=f"U{i}", phone=f"55{i:03}") for i in range(5)]
    reporter.dispatch(contacts, message="hi")

    # Failures 1+2 each followed by a sleep (between iterations).
    # Failure 3+ trip the circuit → skipped → no sleep.
    # Expected: 2 sleeps between attempts 1→2 and 2→3, then circuit trips,
    # remaining iterations are circuit-broken (no API call, no sleep).
    assert len(sleeps) == 2
    assert all(s == 5.0 for s in sleeps)
