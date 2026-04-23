"""Unit tests for webhook/routes/onedrive.py (aiohttp handler)."""
from __future__ import annotations

import asyncio
import os

import pytest
from unittest.mock import AsyncMock, patch
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


@pytest.fixture(autouse=True)
def _env():
    os.environ["GRAPH_WEBHOOK_CLIENT_STATE"] = "good-state"
    yield


@pytest.mark.asyncio
async def test_validation_token_echoed_as_plaintext():
    """Initial Graph handshake — must echo validationToken in <10s."""
    from routes.onedrive import setup_routes

    app = web.Application()
    setup_routes(app)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/onedrive/notify",
            params={"validationToken": "abc-123-xyz"},
        )
        assert resp.status == 200
        assert resp.headers["Content-Type"].startswith("text/plain")
        body = await resp.text()
        assert body == "abc-123-xyz"


@pytest.mark.asyncio
async def test_notification_with_good_client_state_returns_202():
    from routes.onedrive import setup_routes

    app = web.Application()
    setup_routes(app)
    payload = {"value": [{"clientState": "good-state", "resource": "..."}]}
    async with TestClient(TestServer(app)) as client:
        with patch(
            "routes.onedrive.process_notification",
            new=AsyncMock(),
        ) as proc:
            resp = await client.post("/onedrive/notify", json=payload)
            assert resp.status == 202
            # Give the task a moment to run.
            await asyncio.sleep(0.05)
            proc.assert_awaited_once()


@pytest.mark.asyncio
async def test_notification_with_bad_client_state_returns_401():
    from routes.onedrive import setup_routes

    app = web.Application()
    setup_routes(app)
    payload = {"value": [{"clientState": "WRONG", "resource": "..."}]}
    async with TestClient(TestServer(app)) as client:
        with patch(
            "routes.onedrive.process_notification",
            new=AsyncMock(),
        ) as proc:
            resp = await client.post("/onedrive/notify", json=payload)
            assert resp.status == 401
            proc.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_payload_returns_400():
    from routes.onedrive import setup_routes

    app = web.Application()
    setup_routes(app)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/onedrive/notify", data=b"")
        assert resp.status == 400
