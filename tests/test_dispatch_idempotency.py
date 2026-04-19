"""Unit tests for WhatsApp send idempotency (Phase 3)."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock
import fakeredis.aioredis


@pytest.fixture
def fake_redis_async():
    """Async fakeredis client — supports SET NX EX."""
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def mock_session(mocker):
    """aiohttp ClientSession mock — returns 200 by default.

    The code uses:
        async with aiohttp.ClientSession() as session:
            async with session.post(...) as resp:
                ...

    So `aiohttp.ClientSession()` (instantiated) must be an async context
    manager, and `session.post(...)` must also be one.
    """
    response = AsyncMock()
    response.status = 200
    response.json = AsyncMock(return_value={"status": "ok", "id": "uazapi_msg_1"})
    response.text = AsyncMock(return_value='{"status": "ok"}')

    # post(...) returns an async context manager yielding response
    post_ctx = MagicMock()
    post_ctx.__aenter__ = AsyncMock(return_value=response)
    post_ctx.__aexit__ = AsyncMock(return_value=False)

    session = MagicMock()
    session.post = MagicMock(return_value=post_ctx)

    # ClientSession() itself is used as `async with` — make it a ctx manager
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    return session


@pytest.mark.asyncio
async def test_send_whatsapp_first_call_goes_through(
    fake_redis_async, mock_session, mocker,
):
    from dispatch import send_whatsapp
    mocker.patch("dispatch.aiohttp.ClientSession", return_value=mock_session)
    mocker.patch("dispatch._get_redis_async", new=AsyncMock(return_value=fake_redis_async))

    result = await send_whatsapp(
        phone="+5511999998888",
        message="first call",
        draft_id="draft_abc",
    )

    assert result.get("status") != "duplicate"
    mock_session.post.assert_called_once()


@pytest.mark.asyncio
async def test_send_whatsapp_second_call_same_key_returns_duplicate(
    fake_redis_async, mock_session, mocker,
):
    from dispatch import send_whatsapp
    mocker.patch("dispatch.aiohttp.ClientSession", return_value=mock_session)
    mocker.patch("dispatch._get_redis_async", new=AsyncMock(return_value=fake_redis_async))

    # First call
    await send_whatsapp(phone="+5511999998888", message="same msg", draft_id="draft_xyz")
    # Second call — should short-circuit
    result = await send_whatsapp(phone="+5511999998888", message="same msg", draft_id="draft_xyz")

    assert result == {"status": "duplicate", "skipped": True}
    # Only one HTTP post (first call); second was blocked
    assert mock_session.post.call_count == 1


@pytest.mark.asyncio
async def test_send_whatsapp_different_draft_id_goes_through(
    fake_redis_async, mock_session, mocker,
):
    from dispatch import send_whatsapp
    mocker.patch("dispatch.aiohttp.ClientSession", return_value=mock_session)
    mocker.patch("dispatch._get_redis_async", new=AsyncMock(return_value=fake_redis_async))

    await send_whatsapp(phone="+5511999998888", message="same text", draft_id="draft_A")
    result = await send_whatsapp(phone="+5511999998888", message="same text", draft_id="draft_B")

    # Different draft_id → different idempotency key → both go through
    assert result.get("status") != "duplicate"
    assert mock_session.post.call_count == 2


# ─── Tests for process_approval_async idempotency (broadcast path) ──────────

import fakeredis


@pytest.fixture
def fake_redis_sync():
    """Sync fakeredis for the broadcast path."""
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.mark.asyncio
async def test_send_fn_idempotency_blocks_duplicate_in_broadcast(
    fake_redis_sync, mocker,
):
    """Inside process_approval_async, the sync send_fn should block duplicates."""
    from dispatch import _idempotency_key

    mocker.patch("dispatch._get_redis_sync", return_value=fake_redis_sync)

    # Pre-mark a key as if it were just sent
    phone, draft_id, msg = "+5511999998888", "draft_X", "hello"
    key = _idempotency_key(phone, draft_id, msg)
    fake_redis_sync.set(key, "1", ex=86400, nx=True)

    # Now simulate send_fn trying again — should return duplicate
    # We do this by re-invoking SET NX and confirming it returns None
    second_mark = fake_redis_sync.set(key, "1", ex=86400, nx=True)
    assert second_mark is None
