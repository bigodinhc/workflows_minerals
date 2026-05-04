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
