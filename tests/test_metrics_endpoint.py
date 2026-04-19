"""Integration test for the Prometheus /metrics endpoint."""
from __future__ import annotations

import pytest
from aiohttp.test_utils import make_mocked_request


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_200_and_counter_names():
    from routes.api import metrics_endpoint

    request = make_mocked_request("GET", "/metrics")
    response = await metrics_endpoint(request)

    assert response.status == 200
    body = response.body.decode() if isinstance(response.body, bytes) else response.body
    # Counters are registered at module import; they should appear in output (even with zero values)
    assert "whatsapp_messages_total" in body
    assert "telegram_edit_failures_total" in body
    assert "progress_card_edits_total" in body


@pytest.mark.asyncio
async def test_metrics_reflects_incremented_counter():
    from metrics import whatsapp_sent
    from routes.api import metrics_endpoint

    whatsapp_sent.labels(status="success").inc()

    request = make_mocked_request("GET", "/metrics")
    response = await metrics_endpoint(request)
    body = response.body.decode()

    assert 'whatsapp_messages_total{status="success"}' in body
