#!/usr/bin/env python3
"""One-off migration: Google Sheets → Supabase `contacts` table.

Safe to re-run. Idempotent via ON CONFLICT (phone_uazapi) DO NOTHING.

Usage:
  python scripts/migrate_contacts_from_sheets.py --dry-run
  python scripts/migrate_contacts_from_sheets.py
"""
from __future__ import annotations

import argparse
import re
import sys
from typing import Optional

from execution.integrations.sheets_client import SheetsClient
from execution.integrations.contacts_repo import (
    ContactsRepo, normalize_phone, InvalidPhoneError,
)


SHEET_ID = "1tU3Izdo21JichTXg15bc1paWUiN8XioJYZUPpbIUgL0"
SHEET_NAME = "Página1"


def _pick_phone_raw(row: dict) -> Optional[str]:
    """Return the first non-empty phone from columns in priority order.
    Strips 'whatsapp:' prefix only from the From column (the one that has it)."""
    for key in ("Evolution-api", "n8n-evo", "From"):
        v = str(row.get(key, "") or "").strip()
        if not v:
            continue
        if key == "From":
            v = v.replace("whatsapp:", "").strip()
        return v
    return None


def _normalize_for_migration(phone_raw: str) -> str:
    """Migration-only normalizer.

    1. Strip 'whatsapp:' prefix and '@s.whatsapp.net' suffix.
    2. If cleaned digits are 10 or 11 (BR local format without DDI), prepend '55'.
    3. Fall through to phonenumbers-based normalize_phone for final validation.

    This BR fallback lives ONLY in the migration script. The /add flow enforces
    explicit DDI via normalize_phone directly.
    """
    s = str(phone_raw).strip()
    s = s.replace("whatsapp:", "").replace("@s.whatsapp.net", "")
    digits_only = re.sub(r"\D", "", s)
    if len(digits_only) in (10, 11):
        digits_only = "55" + digits_only
    return normalize_phone(digits_only)


def _row_to_payload(row: dict) -> Optional[dict]:
    """Convert a sheet row to a contacts-table insert payload.
    Returns None if the row is unusable (no phone, no name, or invalid phone)."""
    name = str(row.get("ProfileName", "") or "").strip()
    if not name:
        return None
    phone_raw = _pick_phone_raw(row)
    if not phone_raw:
        return None
    try:
        phone_uazapi = _normalize_for_migration(phone_raw)
    except InvalidPhoneError:
        return None
    button_payload = str(row.get("ButtonPayload", "") or "").strip()
    status = "ativo" if button_payload == "Big" else "inativo"
    return {
        "name": name,
        "phone_raw": phone_raw,
        "phone_uazapi": phone_uazapi,
        "status": status,
    }


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print planned inserts without writing.")
    args = parser.parse_args(argv)

    sheets = SheetsClient()
    repo = ContactsRepo()
    rows, _ = sheets.list_contacts(SHEET_ID, sheet_name=SHEET_NAME, per_page=10_000)

    inserted = skipped_invalid = skipped_dup = 0

    for r in rows:
        payload = _row_to_payload(r)
        if payload is None:
            print(f"SKIP (invalid): {r!r}")
            skipped_invalid += 1
            continue

        if args.dry_run:
            print(f"WOULD INSERT: {payload['name']!r} / {payload['phone_uazapi']} / {payload['status']}")
            inserted += 1
            continue

        resp = repo.client.table("contacts").upsert(
            payload,
            on_conflict="phone_uazapi",
            ignore_duplicates=True,
        ).execute()
        if resp.data:
            inserted += 1
            print(f"OK: {payload['name']!r} / {payload['phone_uazapi']} / {payload['status']}")
        else:
            skipped_dup += 1
            print(f"DUP: {payload['name']!r} / {payload['phone_uazapi']}")

    print(
        f"\n{'DRY RUN ' if args.dry_run else ''}RESULT: "
        f"inserted={inserted} skipped_invalid={skipped_invalid} skipped_dup={skipped_dup}"
    )
    return 0 if skipped_invalid == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
