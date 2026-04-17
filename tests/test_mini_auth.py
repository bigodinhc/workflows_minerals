"""Tests for webhook/routes/mini_auth.py — Telegram initData validation."""
from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

TEST_TOKEN = "123456789:AAFakeTokenForTesting_abcdefghijk"


def _make_init_data(
    token: str = TEST_TOKEN,
    user_id: int = 12345,
    first_name: str = "Test",
    extra_params: dict | None = None,
) -> str:
    """Generate a correctly signed Telegram initData string."""
    user = json.dumps({"id": user_id, "first_name": first_name})
    params = {
        "user": user,
        "auth_date": str(int(time.time())),
        **(extra_params or {}),
    }
    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(params.items())
    )
    secret_key = hmac.new(
        key=b"WebAppData", msg=token.encode(), digestmod=hashlib.sha256,
    )
    calculated_hash = hmac.new(
        key=secret_key.digest(),
        msg=data_check_string.encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()
    params["hash"] = calculated_hash
    return urlencode(params)


class FakeRequest:
    """Minimal request-like object for testing."""

    def __init__(self, headers: dict | None = None):
        self.headers = headers or {}


@pytest.mark.asyncio
async def test_valid_init_data():
    from routes.mini_auth import validate_init_data

    init_data = _make_init_data()
    request = FakeRequest(headers={"X-Telegram-Init-Data": init_data})
    with patch("routes.mini_auth.TELEGRAM_BOT_TOKEN", TEST_TOKEN):
        with patch("routes.mini_auth.get_user_role", return_value="admin"):
            result = await validate_init_data(request)
            assert result.user is not None
            assert result.user.id == 12345


@pytest.mark.asyncio
async def test_missing_header_returns_401():
    from aiohttp.web import HTTPUnauthorized
    from routes.mini_auth import validate_init_data

    request = FakeRequest(headers={})
    with patch("routes.mini_auth.TELEGRAM_BOT_TOKEN", TEST_TOKEN):
        with pytest.raises(HTTPUnauthorized):
            await validate_init_data(request)


@pytest.mark.asyncio
async def test_invalid_signature_returns_401():
    from aiohttp.web import HTTPUnauthorized
    from routes.mini_auth import validate_init_data

    request = FakeRequest(headers={"X-Telegram-Init-Data": "user=bad&hash=bad&auth_date=0"})
    with patch("routes.mini_auth.TELEGRAM_BOT_TOKEN", TEST_TOKEN):
        with pytest.raises(HTTPUnauthorized):
            await validate_init_data(request)


@pytest.mark.asyncio
async def test_unknown_user_returns_403():
    from aiohttp.web import HTTPForbidden
    from routes.mini_auth import validate_init_data

    init_data = _make_init_data()
    request = FakeRequest(headers={"X-Telegram-Init-Data": init_data})
    with patch("routes.mini_auth.TELEGRAM_BOT_TOKEN", TEST_TOKEN):
        with patch("routes.mini_auth.get_user_role", return_value="unknown"):
            with pytest.raises(HTTPForbidden):
                await validate_init_data(request)


@pytest.mark.asyncio
async def test_pending_user_returns_403():
    from aiohttp.web import HTTPForbidden
    from routes.mini_auth import validate_init_data

    init_data = _make_init_data()
    request = FakeRequest(headers={"X-Telegram-Init-Data": init_data})
    with patch("routes.mini_auth.TELEGRAM_BOT_TOKEN", TEST_TOKEN):
        with patch("routes.mini_auth.get_user_role", return_value="pending"):
            with pytest.raises(HTTPForbidden):
                await validate_init_data(request)


@pytest.mark.asyncio
async def test_subscriber_allowed():
    from routes.mini_auth import validate_init_data

    init_data = _make_init_data()
    request = FakeRequest(headers={"X-Telegram-Init-Data": init_data})
    with patch("routes.mini_auth.TELEGRAM_BOT_TOKEN", TEST_TOKEN):
        with patch("routes.mini_auth.get_user_role", return_value="subscriber"):
            result = await validate_init_data(request)
            assert result.user.id == 12345
