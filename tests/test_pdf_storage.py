"""Tests for webhook/pdf_storage.py — Supabase Storage upload + signed URL."""
from __future__ import annotations
from unittest.mock import MagicMock, patch
import pytest


@pytest.fixture(autouse=True)
def _supabase_env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role-key")


def test_upload_and_sign_calls_upload_then_signed_url():
    from webhook.pdf_storage import upload_and_sign

    fake_storage_bucket = MagicMock()
    fake_storage_bucket.upload.return_value = {"Key": "approval-1/file.pdf"}
    fake_storage_bucket.create_signed_url.return_value = {
        "signedURL": "https://x.supabase.co/storage/v1/sign/...token=abc"
    }
    fake_client = MagicMock()
    fake_client.storage.from_.return_value = fake_storage_bucket

    with patch("webhook.pdf_storage._client", return_value=fake_client):
        url = upload_and_sign(
            approval_id="approval-1",
            filename="file.pdf",
            pdf_bytes=b"%PDF-fake",
        )

    assert url == "https://x.supabase.co/storage/v1/sign/...token=abc"
    fake_client.storage.from_.assert_called_with("pdf-broadcasts")
    fake_storage_bucket.upload.assert_called_once()
    args, kwargs = fake_storage_bucket.upload.call_args
    # supabase-py upload(path, file, file_options={...})
    assert "approval-1/file.pdf" in (list(args) + list(kwargs.values()))
    fake_storage_bucket.create_signed_url.assert_called_once_with(
        "approval-1/file.pdf", 7 * 24 * 3600
    )


def test_upload_and_sign_overwrites_on_duplicate():
    """Idempotent per (approval_id, filename) — upload includes upsert option."""
    from webhook.pdf_storage import upload_and_sign

    fake_storage_bucket = MagicMock()
    fake_storage_bucket.upload.return_value = {"Key": "approval-2/file.pdf"}
    fake_storage_bucket.create_signed_url.return_value = {"signedURL": "u"}
    fake_client = MagicMock()
    fake_client.storage.from_.return_value = fake_storage_bucket

    with patch("webhook.pdf_storage._client", return_value=fake_client):
        upload_and_sign(
            approval_id="approval-2",
            filename="file.pdf",
            pdf_bytes=b"%PDF-fake",
        )

    _args, kwargs = fake_storage_bucket.upload.call_args
    file_options = kwargs.get("file_options") or {}
    # Either supabase-py >=2: upsert is part of file_options; we accept the
    # common spelling.
    assert (
        file_options.get("upsert") in ("true", True)
        or kwargs.get("upsert") in ("true", True)
    )
