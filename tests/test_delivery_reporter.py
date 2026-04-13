"""Tests for execution.core.delivery_reporter module."""
import pytest
from execution.core.delivery_reporter import Contact, DeliveryResult, DeliveryReport
from datetime import datetime


def test_contact_dataclass():
    c = Contact(name="João Silva", phone="5511999999999")
    assert c.name == "João Silva"
    assert c.phone == "5511999999999"


def test_delivery_result_success():
    c = Contact(name="Ana", phone="5511888888888")
    r = DeliveryResult(contact=c, success=True, error=None, duration_ms=340)
    assert r.success is True
    assert r.error is None


def test_delivery_result_failure():
    c = Contact(name="Ana", phone="5511888888888")
    r = DeliveryResult(contact=c, success=False, error="timeout", duration_ms=30000)
    assert r.success is False
    assert r.error == "timeout"


def test_delivery_report_properties():
    c1 = Contact(name="A", phone="111")
    c2 = Contact(name="B", phone="222")
    c3 = Contact(name="C", phone="333")
    results = [
        DeliveryResult(contact=c1, success=True, error=None, duration_ms=100),
        DeliveryResult(contact=c2, success=False, error="timeout", duration_ms=30000),
        DeliveryResult(contact=c3, success=True, error=None, duration_ms=150),
    ]
    now = datetime.now()
    report = DeliveryReport(
        workflow="test",
        started_at=now,
        finished_at=now,
        results=results,
    )
    assert report.total == 3
    assert report.success_count == 2
    assert report.failure_count == 1
    assert len(report.failures) == 1
    assert report.failures[0].contact.name == "B"


from unittest.mock import MagicMock
from execution.core.delivery_reporter import DeliveryReporter
import requests


def test_dispatch_all_success():
    send_fn = MagicMock()  # does not raise = success
    reporter = DeliveryReporter(workflow="test", send_fn=send_fn, notify_telegram=False)
    contacts = [Contact(name=f"User{i}", phone=f"11{i}") for i in range(5)]
    report = reporter.dispatch(contacts, message="hello")
    assert report.total == 5
    assert report.success_count == 5
    assert report.failure_count == 0
    assert send_fn.call_count == 5


def test_dispatch_partial_failure():
    call_count = {"n": 0}
    def send_fn(phone, text):
        call_count["n"] += 1
        if call_count["n"] in (2, 4):
            raise RuntimeError("boom")
    reporter = DeliveryReporter(workflow="test", send_fn=send_fn, notify_telegram=False)
    contacts = [Contact(name=f"User{i}", phone=f"11{i}") for i in range(5)]
    report = reporter.dispatch(contacts, message="hello")
    assert report.success_count == 3
    assert report.failure_count == 2
    assert all("boom" in r.error for r in report.failures)


def test_dispatch_all_failure():
    def send_fn(phone, text):
        raise RuntimeError("total failure")
    reporter = DeliveryReporter(workflow="test", send_fn=send_fn, notify_telegram=False)
    contacts = [Contact(name="A", phone="111")]
    report = reporter.dispatch(contacts, message="hi")
    assert report.success_count == 0
    assert report.failure_count == 1


def test_error_categorization_timeout():
    def send_fn(phone, text):
        raise requests.Timeout("read timeout")
    reporter = DeliveryReporter(workflow="test", send_fn=send_fn, notify_telegram=False)
    report = reporter.dispatch([Contact(name="A", phone="111")], message="hi")
    assert report.failures[0].error == "timeout"


def test_error_categorization_http_error():
    def send_fn(phone, text):
        resp = requests.Response()
        resp.status_code = 400
        resp._content = b'{"error":"invalid number"}'
        err = requests.HTTPError(response=resp)
        raise err
    reporter = DeliveryReporter(workflow="test", send_fn=send_fn, notify_telegram=False)
    report = reporter.dispatch([Contact(name="A", phone="111")], message="hi")
    assert report.failures[0].error.startswith("HTTP 400")


def test_dispatch_tracks_duration():
    send_fn = MagicMock()
    reporter = DeliveryReporter(workflow="test", send_fn=send_fn, notify_telegram=False)
    report = reporter.dispatch([Contact(name="A", phone="111")], message="hi")
    assert report.results[0].duration_ms >= 0
    assert (report.finished_at - report.started_at).total_seconds() >= 0


import json
import re


def test_dispatch_emits_json_block_on_stdout(capsys):
    send_fn = MagicMock()
    reporter = DeliveryReporter(workflow="test_wf", send_fn=send_fn, notify_telegram=False)
    reporter.dispatch([Contact(name="Ana", phone="5511999")], message="hi")
    captured = capsys.readouterr().out
    assert "<<<DELIVERY_REPORT_START>>>" in captured
    assert "<<<DELIVERY_REPORT_END>>>" in captured


def test_stdout_json_is_parseable(capsys):
    send_fn = MagicMock()
    reporter = DeliveryReporter(workflow="test_wf", send_fn=send_fn, notify_telegram=False)
    reporter.dispatch([
        Contact(name="Ana", phone="5511999"),
        Contact(name="Bob", phone="5511888"),
    ], message="hi")
    captured = capsys.readouterr().out
    match = re.search(
        r"<<<DELIVERY_REPORT_START>>>\s*(\{.*?\})\s*<<<DELIVERY_REPORT_END>>>",
        captured,
        re.DOTALL,
    )
    assert match, "JSON block not found"
    data = json.loads(match.group(1))
    assert data["workflow"] == "test_wf"
    assert data["summary"]["total"] == 2
    assert data["summary"]["success"] == 2
    assert data["summary"]["failure"] == 0
    assert len(data["results"]) == 2
    assert data["results"][0]["name"] == "Ana"
    assert data["results"][0]["phone"] == "5511999"
    assert data["results"][0]["success"] is True
    assert data["results"][0]["error"] is None
    assert "duration_ms" in data["results"][0]


def test_stdout_json_includes_failures(capsys):
    def send_fn(phone, text):
        if phone == "222":
            raise RuntimeError("fail me")
    reporter = DeliveryReporter(workflow="test", send_fn=send_fn, notify_telegram=False)
    reporter.dispatch([
        Contact(name="OK", phone="111"),
        Contact(name="Bad", phone="222"),
    ], message="hi")
    captured = capsys.readouterr().out
    match = re.search(
        r"<<<DELIVERY_REPORT_START>>>\s*(\{.*?\})\s*<<<DELIVERY_REPORT_END>>>",
        captured,
        re.DOTALL,
    )
    data = json.loads(match.group(1))
    assert data["summary"]["failure"] == 1
    fail = [r for r in data["results"] if not r["success"]][0]
    assert fail["name"] == "Bad"
    assert "fail me" in fail["error"]
from execution.core.delivery_reporter import _format_telegram_message


def _make_report(workflow, results):
    from datetime import datetime
    now = datetime.now().astimezone()
    return DeliveryReport(
        workflow=workflow,
        started_at=now,
        finished_at=now,
        results=results,
    )


def test_telegram_message_all_success():
    c = Contact(name="Ana", phone="111")
    results = [DeliveryResult(contact=c, success=True, error=None, duration_ms=100)]
    report = _make_report("morning_check", results)
    msg = _format_telegram_message(report, dashboard_base_url="https://dash", gh_run_id=None)
    assert "✅" in msg
    assert "MORNING CHECK" in msg
    assert "Total: 1" in msg
    assert "OK: 1" in msg
    assert "Falha: 0" in msg


def test_telegram_message_with_failures():
    results = [
        DeliveryResult(contact=Contact(name="A", phone="111"), success=True, error=None, duration_ms=100),
        DeliveryResult(contact=Contact(name="Carlos", phone="222"), success=False, error="timeout", duration_ms=30000),
    ]
    report = _make_report("test", results)
    msg = _format_telegram_message(report, dashboard_base_url="https://dash", gh_run_id=None)
    assert "⚠️" in msg
    assert "Carlos" in msg
    assert "222" in msg
    assert "timeout" in msg


def test_telegram_message_total_failure():
    results = [
        DeliveryResult(contact=Contact(name=f"U{i}", phone=str(i)), success=False, error="boom", duration_ms=100)
        for i in range(10)
    ]
    report = _make_report("test", results)
    msg = _format_telegram_message(report, dashboard_base_url="https://dash", gh_run_id=None)
    assert "🚨" in msg
    assert "FALHA TOTAL" in msg


def test_telegram_message_truncates_long_failure_list():
    results = [
        DeliveryResult(contact=Contact(name=f"U{i}", phone=str(i)), success=False, error="boom", duration_ms=100)
        for i in range(50)
    ]
    report = _make_report("test", results)
    msg = _format_telegram_message(report, dashboard_base_url="https://dash", gh_run_id=None)
    assert "...e mais 35" in msg


def test_telegram_message_includes_run_id_link():
    results = [DeliveryResult(contact=Contact(name="A", phone="111"), success=True, error=None, duration_ms=100)]
    report = _make_report("test", results)
    msg = _format_telegram_message(report, dashboard_base_url="https://dash.com", gh_run_id="999")
    assert "https://dash.com/?run_id=999" in msg


def test_telegram_message_home_link_when_no_run_id():
    results = [DeliveryResult(contact=Contact(name="A", phone="111"), success=True, error=None, duration_ms=100)]
    report = _make_report("test", results)
    msg = _format_telegram_message(report, dashboard_base_url="https://dash.com", gh_run_id=None)
    assert "https://dash.com/" in msg
    assert "?run_id=" not in msg
