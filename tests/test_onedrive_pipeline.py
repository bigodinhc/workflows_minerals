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
