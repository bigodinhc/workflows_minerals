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
    # JSON error field is extracted cleanly, not dumped raw
    assert report.failures[0].error == "HTTP 400: invalid number"


def test_error_categorization_extracts_json_message_field():
    def send_fn(phone, text):
        resp = requests.Response()
        resp.status_code = 500
        resp._content = b'{"message":"rate limit exceeded"}'
        raise requests.HTTPError(response=resp)
    reporter = DeliveryReporter(workflow="test", send_fn=send_fn, notify_telegram=False)
    report = reporter.dispatch([Contact(name="A", phone="111")], message="hi")
    assert report.failures[0].error == "HTTP 500: rate limit exceeded"


def test_error_categorization_falls_back_to_raw_body_when_not_json():
    def send_fn(phone, text):
        resp = requests.Response()
        resp.status_code = 503
        resp._content = b'Service Unavailable'
        raise requests.HTTPError(response=resp)
    reporter = DeliveryReporter(workflow="test", send_fn=send_fn, notify_telegram=False)
    report = reporter.dispatch([Contact(name="A", phone="111")], message="hi")
    assert report.failures[0].error.startswith("HTTP 503")
    assert "Service Unavailable" in report.failures[0].error


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


def test_dispatch_sends_telegram_when_enabled(monkeypatch):
    send_calls = []

    class FakeTelegram:
        def __init__(self):
            pass

        def send_message(self, text, chat_id=None, **kwargs):
            send_calls.append({"text": text, "chat_id": chat_id})
            return 1

    monkeypatch.setattr(
        "execution.core.delivery_reporter._build_telegram_client",
        lambda: FakeTelegram(),
    )

    reporter = DeliveryReporter(
        workflow="test",
        send_fn=MagicMock(),
        notify_telegram=True,
        telegram_chat_id="123",
    )
    reporter.dispatch([Contact(name="A", phone="111")], message="hi")
    assert len(send_calls) == 1
    assert "test".upper() in send_calls[0]["text"].upper()
    assert send_calls[0]["chat_id"] == "123"


def test_dispatch_skips_telegram_when_disabled():
    reporter = DeliveryReporter(
        workflow="test",
        send_fn=MagicMock(),
        notify_telegram=False,
    )
    report = reporter.dispatch([Contact(name="A", phone="111")], message="hi")
    assert report.total == 1


def test_dispatch_continues_when_telegram_fails(monkeypatch):
    class BrokenTelegram:
        def send_message(self, text, chat_id=None, **kwargs):
            raise RuntimeError("telegram down")

    monkeypatch.setattr(
        "execution.core.delivery_reporter._build_telegram_client",
        lambda: BrokenTelegram(),
    )
    reporter = DeliveryReporter(
        workflow="test",
        send_fn=MagicMock(),
        notify_telegram=True,
    )
    report = reporter.dispatch([Contact(name="A", phone="111")], message="hi")
    assert report.total == 1
    assert report.success_count == 1


def test_on_progress_called_per_contact():
    events = []

    def on_progress(processed, total, result):
        events.append((processed, total, result.contact.name))

    reporter = DeliveryReporter(
        workflow="test",
        send_fn=MagicMock(),
        notify_telegram=False,
    )
    reporter.dispatch(
        [Contact(name=f"U{i}", phone=str(i)) for i in range(3)],
        message="hi",
        on_progress=on_progress,
    )
    assert len(events) == 3
    assert events[0] == (1, 3, "U0")
    assert events[1] == (2, 3, "U1")
    assert events[2] == (3, 3, "U2")


def test_on_progress_exception_does_not_abort():
    def on_progress(processed, total, result):
        raise RuntimeError("callback broken")

    reporter = DeliveryReporter(
        workflow="test",
        send_fn=MagicMock(),
        notify_telegram=False,
    )
    report = reporter.dispatch(
        [Contact(name="A", phone="111"), Contact(name="B", phone="222")],
        message="hi",
        on_progress=on_progress,
    )
    assert report.total == 2
    assert report.success_count == 2
from execution.core.delivery_reporter import build_contact_from_row


def test_build_contact_uses_profile_name_first():
    row = {"ProfileName": "Joao Silva", "Nome": "Wrong", "From": "whatsapp:+5511999"}
    c = build_contact_from_row(row)
    assert c.name == "Joao Silva"


def test_build_contact_falls_back_to_nome():
    row = {"Nome": "Maria", "From": "whatsapp:+5521888"}
    c = build_contact_from_row(row)
    assert c.name == "Maria"


def test_build_contact_falls_back_to_name():
    row = {"Name": "Carlos", "From": "whatsapp:+5531777"}
    c = build_contact_from_row(row)
    assert c.name == "Carlos"


def test_build_contact_name_placeholder_when_missing():
    row = {"From": "whatsapp:+5511999"}
    c = build_contact_from_row(row)
    assert c.name == "—"


def test_build_contact_phone_from_evolution_api_column():
    row = {"ProfileName": "A", "Evolution-api": "5511999999999"}
    c = build_contact_from_row(row)
    assert c.phone == "5511999999999"


def test_build_contact_phone_from_n8n_evo_column():
    row = {"ProfileName": "A", "n8n-evo": "5511999999999@s.whatsapp.net"}
    c = build_contact_from_row(row)
    assert c.phone == "5511999999999"


def test_build_contact_phone_from_from_column():
    row = {"ProfileName": "A", "From": "whatsapp:+5511999999999"}
    c = build_contact_from_row(row)
    assert c.phone == "5511999999999"


def test_build_contact_phone_strips_prefixes_and_suffixes():
    row = {"ProfileName": "A", "From": "whatsapp:+5511 999-99999"}
    c = build_contact_from_row(row)
    # After strip: whatsapp: gone, + gone, spaces/hyphens kept as-is (not digits)
    assert c.phone == "5511 999-99999"  # spaces/hyphens preserved, plus/whatsapp stripped


def test_build_contact_returns_none_when_no_phone():
    row = {"ProfileName": "Ghost"}
    assert build_contact_from_row(row) is None


def test_build_contact_returns_none_when_phone_empty():
    row = {"ProfileName": "Ghost", "From": ""}
    assert build_contact_from_row(row) is None


def _mock_http_error(status: int, body: str) -> requests.HTTPError:
    """Build a requests.HTTPError with a fake response for testing _categorize_error."""
    response = MagicMock(spec=requests.Response)
    response.status_code = status
    response.text = body
    exc = requests.HTTPError(f"{status} Server Error", response=response)
    return exc


def test_categorize_error_prefers_message_over_boolean_error_field():
    """UazAPI returns {"error": true, "message": "WhatsApp disconnected"}.
    Must use 'message', not stringify the boolean 'error' to 'True'.
    """
    from execution.core.delivery_reporter import _categorize_error

    exc = _mock_http_error(503, '{"error":true,"message":"WhatsApp disconnected"}')
    result = _categorize_error(exc)
    assert "WhatsApp disconnected" in result
    assert "True" not in result


def test_categorize_error_uses_error_field_when_it_is_a_string():
    """Some upstreams return {"error": "rate limited"} — string, not bool. Keep using it."""
    from execution.core.delivery_reporter import _categorize_error

    exc = _mock_http_error(429, '{"error":"rate limited"}')
    result = _categorize_error(exc)
    assert "rate limited" in result


def test_send_error_category_is_enum():
    from execution.core.delivery_reporter import SendErrorCategory
    assert SendErrorCategory.WHATSAPP_DISCONNECTED.value == "whatsapp_disconnected"
    assert SendErrorCategory.RATE_LIMIT.value == "rate_limit"
    assert SendErrorCategory.INVALID_NUMBER.value == "invalid_number"
    assert SendErrorCategory.UPSTREAM_5XX.value == "upstream_5xx"
    assert SendErrorCategory.AUTH.value == "auth"
    assert SendErrorCategory.TIMEOUT.value == "timeout"
    assert SendErrorCategory.NETWORK.value == "network"
    assert SendErrorCategory.UNKNOWN.value == "unknown"


def test_classify_error_whatsapp_disconnected():
    from execution.core.delivery_reporter import classify_error, SendErrorCategory

    exc = _mock_http_error(503, '{"error":true,"message":"WhatsApp disconnected"}')
    category, reason = classify_error(exc)
    assert category == SendErrorCategory.WHATSAPP_DISCONNECTED
    assert "WhatsApp disconnected" in reason


def test_classify_error_rate_limit_429():
    from execution.core.delivery_reporter import classify_error, SendErrorCategory

    exc = _mock_http_error(429, '{"error":"rate limited"}')
    category, _ = classify_error(exc)
    assert category == SendErrorCategory.RATE_LIMIT


def test_classify_error_invalid_number_400():
    from execution.core.delivery_reporter import classify_error, SendErrorCategory

    exc = _mock_http_error(400, '{"error":"number not registered on whatsapp"}')
    category, _ = classify_error(exc)
    assert category == SendErrorCategory.INVALID_NUMBER


def test_classify_error_auth_401():
    from execution.core.delivery_reporter import classify_error, SendErrorCategory

    exc = _mock_http_error(401, '{"error":"invalid token"}')
    category, _ = classify_error(exc)
    assert category == SendErrorCategory.AUTH


def test_classify_error_generic_upstream_500():
    from execution.core.delivery_reporter import classify_error, SendErrorCategory

    exc = _mock_http_error(500, '{"error":"internal server error"}')
    category, _ = classify_error(exc)
    assert category == SendErrorCategory.UPSTREAM_5XX


def test_classify_error_timeout():
    from execution.core.delivery_reporter import classify_error, SendErrorCategory

    exc = requests.Timeout("read timed out")
    category, reason = classify_error(exc)
    assert category == SendErrorCategory.TIMEOUT
    assert "timeout" in reason.lower()


def test_classify_error_connection_error_is_network():
    from execution.core.delivery_reporter import classify_error, SendErrorCategory

    exc = requests.ConnectionError("connection refused")
    category, _ = classify_error(exc)
    assert category == SendErrorCategory.NETWORK


def test_classify_error_unknown_exception():
    from execution.core.delivery_reporter import classify_error, SendErrorCategory

    category, reason = classify_error(ValueError("weird"))
    assert category == SendErrorCategory.UNKNOWN
    assert "weird" in reason


def test_classify_error_http_error_without_response_is_unknown():
    """HTTPError with no attached response (pre-flight failures) falls to UNKNOWN,
    not UPSTREAM_5XX — Task 5 circuit breaker relies on this not being fatal."""
    from execution.core.delivery_reporter import classify_error, SendErrorCategory
    exc = requests.HTTPError("no response attached")
    category, _ = classify_error(exc)
    assert category == SendErrorCategory.UNKNOWN
