"""Unit tests for callbacks_onedrive router handlers."""
from __future__ import annotations

import json
import pytest
import fakeredis.aioredis
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def redis_client():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def seeded_pending_factory(redis_client):
    """Returns an async setup func: await it inside each async test."""
    async def _setup():
        state = {
            "drive_id": "drive-test",
            "drive_item_id": "item-1",
            "filename": "Test.pdf",
            "size": 1024,
            "downloadUrl": "https://x",
            "downloadUrl_fetched_at": "2026-04-22T00:00:00+00:00",
            "status": "pending",
            "created_at": "2026-04-22T00:00:00+00:00",
        }
        await redis_client.set("approval:abc12", json.dumps(state))
        return "abc12"
    return _setup


@pytest.mark.asyncio
async def test_on_approve_shows_confirm_screen(
    mock_bot, mock_callback_query, redis_client, seeded_pending_factory
):
    from bot.routers.callbacks_onedrive import on_approve
    from bot.callback_data import OneDriveApprove

    approval_id = await seeded_pending_factory()
    cb_data = OneDriveApprove(approval_id=approval_id, list_code="minerals_report")
    cb = mock_callback_query(data=cb_data.pack())
    cb.bot = mock_bot

    mock_repo = MagicMock()
    mock_list = MagicMock(code="minerals_report", label="Minerals Report", member_count=3)
    mock_repo.list_by_list_code.return_value = [MagicMock() for _ in range(3)]
    mock_repo.list_lists.return_value = [mock_list]

    with patch("bot.routers.callbacks_onedrive._redis", return_value=redis_client), \
         patch("bot.routers.callbacks_onedrive.ContactsRepo", return_value=mock_repo):
        await on_approve(cb, cb_data)

    mock_bot.edit_message_text.assert_called_once()
    edited = mock_bot.edit_message_text.call_args.kwargs["text"]
    assert "Confirmar" in edited
    assert "Minerals Report" in edited

    stored = json.loads(await redis_client.get(f"approval:{approval_id}"))
    assert stored["status"] == "awaiting_confirm"


@pytest.mark.asyncio
async def test_on_approve_all_uses_list_active_count(
    mock_bot, mock_callback_query, redis_client, seeded_pending_factory
):
    from bot.routers.callbacks_onedrive import on_approve
    from bot.callback_data import OneDriveApprove

    approval_id = await seeded_pending_factory()
    cb_data = OneDriveApprove(approval_id=approval_id, list_code="__all__")
    cb = mock_callback_query(data=cb_data.pack())
    cb.bot = mock_bot

    mock_repo = MagicMock()
    mock_repo.list_active.return_value = [MagicMock() for _ in range(62)]

    with patch("bot.routers.callbacks_onedrive._redis", return_value=redis_client), \
         patch("bot.routers.callbacks_onedrive.ContactsRepo", return_value=mock_repo):
        await on_approve(cb, cb_data)

    edited = mock_bot.edit_message_text.call_args.kwargs["text"]
    assert "62" in edited
    assert "Todos" in edited or "todos" in edited.lower()


@pytest.mark.asyncio
async def test_on_discard_edits_card_and_deletes_state(
    mock_bot, mock_callback_query, redis_client, seeded_pending_factory
):
    from bot.routers.callbacks_onedrive import on_discard
    from bot.callback_data import OneDriveDiscard

    approval_id = await seeded_pending_factory()
    cb_data = OneDriveDiscard(approval_id=approval_id)
    cb = mock_callback_query(data=cb_data.pack())
    cb.bot = mock_bot

    with patch("bot.routers.callbacks_onedrive._redis", return_value=redis_client):
        await on_discard(cb, cb_data)

    edited = mock_bot.edit_message_text.call_args.kwargs["text"]
    assert "Descartado" in edited or "❌" in edited
    assert (await redis_client.get(f"approval:{approval_id}")) is None


@pytest.mark.asyncio
async def test_on_confirm_triggers_dispatch(
    mock_bot, mock_callback_query, redis_client, seeded_pending_factory
):
    from bot.routers.callbacks_onedrive import on_confirm
    from bot.callback_data import OneDriveConfirm

    approval_id = await seeded_pending_factory()
    cb_data = OneDriveConfirm(approval_id=approval_id, list_code="minerals_report")
    cb = mock_callback_query(data=cb_data.pack())
    cb.bot = mock_bot

    mock_dispatch = AsyncMock(return_value={"sent": 3, "failed": 0, "skipped": 0})
    mock_repo = MagicMock()
    mock_list = MagicMock(code="minerals_report", label="Minerals Report", member_count=3)
    mock_repo.list_lists.return_value = [mock_list]
    with patch("bot.routers.callbacks_onedrive._redis", return_value=redis_client), \
         patch("bot.routers.callbacks_onedrive.ContactsRepo", return_value=mock_repo), \
         patch("bot.routers.callbacks_onedrive.dispatch_document", mock_dispatch):
        await on_confirm(cb, cb_data)

    mock_dispatch.assert_awaited_once()
    # Either positional or kwarg form
    kwargs = mock_dispatch.call_args.kwargs
    args = mock_dispatch.call_args.args
    assert kwargs.get("approval_id", args[0] if args else None) == approval_id


@pytest.mark.asyncio
async def test_expired_approval_shows_warning(
    mock_bot, mock_callback_query, redis_client
):
    from bot.routers.callbacks_onedrive import on_approve
    from bot.callback_data import OneDriveApprove

    cb_data = OneDriveApprove(approval_id="nonexistent", list_code="minerals_report")
    cb = mock_callback_query(data=cb_data.pack())
    cb.bot = mock_bot

    with patch("bot.routers.callbacks_onedrive._redis", return_value=redis_client):
        await on_approve(cb, cb_data)

    cb.answer.assert_called()
    args_list = cb.answer.call_args
    text = args_list.kwargs.get("text") or (args_list.args[0] if args_list.args else "")
    assert "expirada" in str(text).lower() or "expired" in str(text).lower()


# ── Task 4: _claim helper tests ──


@pytest.mark.asyncio
async def test_claim_winner_path(redis_client):
    """First click on a fresh approval → returns ('won', claimer_dict)."""
    from bot.routers.callbacks_onedrive import _claim
    await redis_client.set(
        "approval:abc12",
        json.dumps({"status": "pending"}),
        ex=48 * 3600,
    )
    user = MagicMock()
    user.id = 100
    user.username = "joao"
    user.first_name = "João"

    status, claimer = await _claim(redis_client, "abc12", user)

    assert status == "won"
    assert claimer["chat_id"] == 100
    assert claimer["label"] == "@joao"
    # Persisted in Redis
    raw = await redis_client.get("approval:abc12:claimed_by")
    assert raw is not None
    persisted = json.loads(raw)
    assert persisted["chat_id"] == 100


@pytest.mark.asyncio
async def test_claim_loser_path(redis_client):
    """Second click by a different user → returns ('lost', original_claimer)."""
    from bot.routers.callbacks_onedrive import _claim
    await redis_client.set(
        "approval:abc12",
        json.dumps({"status": "pending"}),
        ex=48 * 3600,
    )
    # Pre-existing claim by user A
    await redis_client.set(
        "approval:abc12:claimed_by",
        json.dumps({"chat_id": 100, "label": "@joao", "claimed_at": "x"}),
        ex=48 * 3600,
    )

    user_b = MagicMock()
    user_b.id = 200
    user_b.username = "maria"
    user_b.first_name = "Maria"

    status, claimer = await _claim(redis_client, "abc12", user_b)

    assert status == "lost"
    assert claimer["chat_id"] == 100  # original claimer
    assert claimer["label"] == "@joao"


@pytest.mark.asyncio
async def test_claim_reentrant_path(redis_client):
    """Same user clicks twice → second call returns ('reentrant', self_claimer)."""
    from bot.routers.callbacks_onedrive import _claim
    await redis_client.set(
        "approval:abc12",
        json.dumps({"status": "pending"}),
        ex=48 * 3600,
    )
    user = MagicMock()
    user.id = 100
    user.username = "joao"
    user.first_name = "João"

    status1, _ = await _claim(redis_client, "abc12", user)
    status2, claimer2 = await _claim(redis_client, "abc12", user)

    assert status1 == "won"
    assert status2 == "reentrant"
    assert claimer2["chat_id"] == 100


@pytest.mark.asyncio
async def test_claim_inherits_approval_ttl(redis_client):
    """claimed_by key TTL ≈ approval key remaining TTL (within 5 s)."""
    from bot.routers.callbacks_onedrive import _claim
    await redis_client.set(
        "approval:abc12",
        json.dumps({"status": "pending"}),
        ex=48 * 3600,
    )
    user = MagicMock()
    user.id = 100
    user.username = "j"
    user.first_name = "J"

    await _claim(redis_client, "abc12", user)
    approval_ttl = await redis_client.ttl("approval:abc12")
    claim_ttl = await redis_client.ttl("approval:abc12:claimed_by")

    assert abs(approval_ttl - claim_ttl) <= 5
