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
