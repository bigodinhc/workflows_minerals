"""Supabase Storage helper for the OneDrive PDF link-delivery mode.

Uploads a PDF into the `pdf-broadcasts` bucket (private) and returns a
7-day signed URL. Idempotent: re-uploading the same (approval_id, filename)
overwrites in place via upsert, so retried dispatches do not 409.

The bucket must exist and be private; no public read policy. Only signed
URLs hand out access.
"""
from __future__ import annotations

import os
from typing import Optional

from supabase import create_client, Client


BUCKET = "pdf-broadcasts"
SIGNED_URL_TTL_SECONDS = 7 * 24 * 3600  # 7 days


_cached_client: Optional[Client] = None


def _client() -> Client:
    """Lazy-cached service-role Supabase client."""
    global _cached_client
    if _cached_client is None:
        url = os.environ["SUPABASE_URL"]
        key = (
            os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
            or os.environ.get("SUPABASE_KEY")
        )
        if not key:
            raise RuntimeError(
                "SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_KEY) must be set"
            )
        _cached_client = create_client(url, key)
    return _cached_client


def upload_and_sign(
    approval_id: str, filename: str, pdf_bytes: bytes
) -> str:
    """Upload PDF bytes and return a 7-day signed URL.

    Path scheme: `<approval_id>/<filename>` inside the `pdf-broadcasts`
    bucket. Subsequent calls with the same key overwrite (upsert).
    """
    path = f"{approval_id}/{filename}"
    bucket = _client().storage.from_(BUCKET)

    bucket.upload(
        path,
        pdf_bytes,
        file_options={
            "content-type": "application/pdf",
            "upsert": "true",  # supabase-py expects string here
        },
    )
    signed = bucket.create_signed_url(path, SIGNED_URL_TTL_SECONDS)
    url = signed.get("signedURL") or ""
    if not url:
        raise RuntimeError(
            f"Supabase create_signed_url returned no signedURL for {path}"
        )
    return url
