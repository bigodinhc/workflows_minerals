"""Unit tests for webhook/onedrive_pipeline.py — detection & approval card."""
from __future__ import annotations

import json
import pytest
import fakeredis.aioredis
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def redis_client():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def sample_pdf_item():
    return {
        "id": "item-minerals-042226",
        "name": "Minerals_Report_042226.pdf",
        "size": 1258291,
        "file": {"mimeType": "application/pdf"},
        "@microsoft.graph.downloadUrl": "https://cdn.example.com/x?sig=abc",
    }


@pytest.fixture
def sample_folder_item():
    return {"id": "folder-1", "name": "Subfolder", "folder": {"childCount": 0}}


@pytest.fixture
def fake_contacts_repo():
    repo = MagicMock()
    ml = MagicMock(code="minerals_report", label="Minerals Report", member_count=45)
    sf = MagicMock(code="solid_fuels",     label="Solid Fuels",     member_count=12)
    ti = MagicMock(code="time_interno",    label="Time Interno",    member_count=8)
    repo.list_lists.return_value = [ml, sf, ti]
    repo.list_active.return_value = [MagicMock() for _ in range(62)]
    return repo


@pytest.mark.asyncio
async def test_filter_drops_folders(sample_folder_item):
    from onedrive_pipeline import _is_pdf_file
    assert _is_pdf_file(sample_folder_item) is False


@pytest.mark.asyncio
async def test_filter_accepts_pdf_by_mime_type(sample_pdf_item):
    from onedrive_pipeline import _is_pdf_file
    assert _is_pdf_file(sample_pdf_item) is True


@pytest.mark.asyncio
async def test_filter_accepts_pdf_by_extension():
    from onedrive_pipeline import _is_pdf_file
    item = {"name": "Relatorio.PDF", "file": {"mimeType": "application/octet-stream"}}
    assert _is_pdf_file(item) is True


@pytest.mark.asyncio
async def test_filter_rejects_non_pdf_files():
    from onedrive_pipeline import _is_pdf_file
    item = {"name": "image.png", "file": {"mimeType": "image/png"}}
    assert _is_pdf_file(item) is False


@pytest.mark.asyncio
async def test_dedup_skips_already_seen_items(redis_client, sample_pdf_item):
    from onedrive_pipeline import _is_new_item
    await redis_client.set(f"seen:onedrive:{sample_pdf_item['id']}", "1")
    assert await _is_new_item(redis_client, sample_pdf_item["id"]) is False


@pytest.mark.asyncio
async def test_dedup_accepts_unseen_items(redis_client, sample_pdf_item):
    from onedrive_pipeline import _is_new_item
    assert await _is_new_item(redis_client, sample_pdf_item["id"]) is True


@pytest.mark.asyncio
async def test_mark_seen_sets_30day_ttl(redis_client, sample_pdf_item):
    from onedrive_pipeline import _mark_seen
    await _mark_seen(redis_client, sample_pdf_item["id"])
    ttl = await redis_client.ttl(f"seen:onedrive:{sample_pdf_item['id']}")
    assert 29 * 24 * 3600 < ttl <= 30 * 24 * 3600


@pytest.mark.asyncio
async def test_create_approval_stores_state_with_48h_ttl(redis_client, sample_pdf_item):
    from onedrive_pipeline import create_approval_state
    approval_id = await create_approval_state(
        redis_client, sample_pdf_item, drive_id="drive-test"
    )
    assert approval_id
    stored = await redis_client.get(f"approval:{approval_id}")
    data = json.loads(stored)
    assert data["drive_item_id"] == sample_pdf_item["id"]
    assert data["filename"] == sample_pdf_item["name"]
    assert data["downloadUrl"] == sample_pdf_item["@microsoft.graph.downloadUrl"]
    assert data["status"] == "pending"
    ttl = await redis_client.ttl(f"approval:{approval_id}")
    assert 47 * 3600 < ttl <= 48 * 3600


@pytest.mark.asyncio
async def test_build_approval_keyboard_has_all_lists_plus_todos_plus_discard(
    fake_contacts_repo
):
    from onedrive_pipeline import build_approval_keyboard
    kb = build_approval_keyboard(
        approval_id="abc-123",
        contacts_repo=fake_contacts_repo,
    )
    # Inline keyboard is a list of rows of buttons.
    flat_labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("Minerals Report" in l and "45" in l for l in flat_labels)
    assert any("Solid Fuels" in l and "12" in l for l in flat_labels)
    assert any("Time Interno" in l and "8" in l for l in flat_labels)
    assert any("Todos" in l and "62" in l for l in flat_labels)
    assert any("Descartar" in l for l in flat_labels)


@pytest.mark.asyncio
async def test_process_notification_rejects_wrong_client_state():
    from onedrive_pipeline import validate_notification
    payload = {"value": [{"clientState": "WRONG"}]}
    assert validate_notification(payload, expected_client_state="GOOD") is False


@pytest.mark.asyncio
async def test_process_notification_accepts_correct_client_state():
    from onedrive_pipeline import validate_notification
    payload = {"value": [{"clientState": "GOOD"}, {"clientState": "GOOD"}]}
    assert validate_notification(payload, expected_client_state="GOOD") is True


@pytest.mark.asyncio
async def test_create_approval_persists_trace_id(redis_client, sample_pdf_item):
    from onedrive_pipeline import create_approval_state
    approval_id = await create_approval_state(
        redis_client, sample_pdf_item, drive_id="drive-test", trace_id="trace-abc"
    )
    data = json.loads(await redis_client.get(f"approval:{approval_id}"))
    assert data["trace_id"] == "trace-abc"


# ── Multi-recipient fan-out tests (Task 3 of multi-approver plan) ──


@pytest.fixture
def fake_full_item():
    """Item shape after graph.get_item — has a downloadUrl."""
    return {
        "id": "item-multi-1",
        "name": "Multi_Test.pdf",
        "size": 9999,
        "file": {"mimeType": "application/pdf"},
        "@microsoft.graph.downloadUrl": "https://cdn.example.com/x?sig=multi",
    }


@pytest.mark.asyncio
async def test_send_approval_cards_admin_only_when_env_empty(
    monkeypatch, redis_client, fake_full_item, fake_contacts_repo
):
    """Empty ONEDRIVE_APPROVER_IDS → 1 send to admin, recipients=[admin]."""
    from onedrive_pipeline import _send_approval_cards
    monkeypatch.delenv("ONEDRIVE_APPROVER_IDS", raising=False)
    from bot.users import get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()

    bot = AsyncMock()
    bot.send_message = AsyncMock(side_effect=[
        MagicMock(message_id=1001),
    ])

    fanout = await _send_approval_cards(
        bot=bot,
        admin_chat_id=100,
        text="hello",
        keyboard=MagicMock(),
    )
    recipients = fanout["recipients"]

    assert recipients == [{"chat_id": 100, "message_id": 1001}]
    assert bot.send_message.await_count == 1


@pytest.mark.asyncio
async def test_send_approval_cards_admin_plus_approvers(
    monkeypatch, fake_full_item
):
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "200,300")
    from onedrive_pipeline import _send_approval_cards
    from bot.users import get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()

    bot = AsyncMock()
    bot.send_message = AsyncMock(side_effect=[
        MagicMock(message_id=1001),
        MagicMock(message_id=2002),
        MagicMock(message_id=3003),
    ])

    fanout = await _send_approval_cards(
        bot=bot,
        admin_chat_id=100,
        text="hello",
        keyboard=MagicMock(),
    )
    recipients = fanout["recipients"]

    chat_ids = sorted(r["chat_id"] for r in recipients)
    assert chat_ids == [100, 200, 300]
    assert bot.send_message.await_count == 3


@pytest.mark.asyncio
async def test_send_approval_cards_dedup_admin_in_env(monkeypatch):
    """Admin chat_id listed in env → still only one send to admin."""
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "100,200")
    from onedrive_pipeline import _send_approval_cards
    from bot.users import get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()

    bot = AsyncMock()
    bot.send_message = AsyncMock(side_effect=[
        MagicMock(message_id=1001),
        MagicMock(message_id=2002),
    ])

    fanout = await _send_approval_cards(
        bot=bot,
        admin_chat_id=100,
        text="hello",
        keyboard=MagicMock(),
    )
    recipients = fanout["recipients"]

    chat_ids = sorted(r["chat_id"] for r in recipients)
    assert chat_ids == [100, 200]
    assert bot.send_message.await_count == 2


@pytest.mark.asyncio
async def test_send_approval_cards_partial_failure_continues(monkeypatch):
    """One approver send fails → others still proceed; recipients excludes the failure."""
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "200,300")
    from onedrive_pipeline import _send_approval_cards
    from bot.users import get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()

    class _Forbidden(Exception):
        pass

    bot = AsyncMock()
    bot.send_message = AsyncMock(side_effect=[
        MagicMock(message_id=1001),
        _Forbidden("blocked"),
        MagicMock(message_id=3003),
    ])

    fanout = await _send_approval_cards(
        bot=bot,
        admin_chat_id=100,
        text="hello",
        keyboard=MagicMock(),
    )
    recipients = fanout["recipients"]

    chat_ids = sorted(r["chat_id"] for r in recipients)
    assert chat_ids == [100, 300]
    assert bot.send_message.await_count == 3


@pytest.mark.asyncio
async def test_send_approval_cards_admin_failure_returns_empty(monkeypatch):
    """If admin send fails too — return empty list, caller decides what to do."""
    monkeypatch.delenv("ONEDRIVE_APPROVER_IDS", raising=False)
    from onedrive_pipeline import _send_approval_cards
    from bot.users import get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()

    bot = AsyncMock()
    bot.send_message = AsyncMock(side_effect=Exception("network down"))

    fanout = await _send_approval_cards(
        bot=bot,
        admin_chat_id=100,
        text="hello",
        keyboard=MagicMock(),
    )

    assert fanout["recipients"] == []
    assert bot.send_message.await_count == 1


@pytest.mark.asyncio
async def test_persist_recipients_updates_approval_state(redis_client):
    """After _persist_recipients, approval state JSON has recipients array."""
    from onedrive_pipeline import _persist_recipients

    await redis_client.set(
        "approval:abc12",
        json.dumps({"status": "pending", "filename": "x.pdf"}),
        ex=48 * 3600,
    )

    await _persist_recipients(
        redis_client,
        approval_id="abc12",
        recipients=[
            {"chat_id": 100, "message_id": 1001},
            {"chat_id": 200, "message_id": 2002},
        ],
    )

    raw = await redis_client.get("approval:abc12")
    state = json.loads(raw)
    assert state["recipients"] == [
        {"chat_id": 100, "message_id": 1001},
        {"chat_id": 200, "message_id": 2002},
    ]
    assert state["filename"] == "x.pdf"  # other fields preserved
    ttl = await redis_client.ttl("approval:abc12")
    assert ttl > 0  # KEEPTTL preserved
