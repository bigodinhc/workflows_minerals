"""Repository for the Supabase `contacts` table.

Replaces execution/integrations/sheets_client.py. Consumers:
  - execution/scripts/{morning_check,send_news,send_daily_report,baltic_ingestion}.py
  - webhook/dispatch.py
  - webhook/bot/routers/{commands,messages,callbacks_contacts}.py
  - dashboard/app/api/contacts/route.ts (parallel TS implementation)
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Callable, Optional

import phonenumbers


# ── Exceptions ──

class ContactNotFoundError(Exception):
    """No contact matches the given phone/id."""


class ContactAlreadyExistsError(Exception):
    """add() would create a duplicate phone_uazapi."""
    def __init__(self, existing: "Contact"):
        self.existing = existing
        super().__init__(
            f"Contact {existing.name!r} already exists "
            f"({existing.phone_uazapi}, status={existing.status})"
        )


class InvalidPhoneError(ValueError):
    """normalize_phone rejected the input."""


# ── Phone normalization ──

def normalize_phone(raw) -> str:
    """Parse and validate a user-supplied phone string, return the canonical
    uazapi-ready form: digits only, no '+', E.164 internally.

    Uses Google's libphonenumber via the `phonenumbers` library.

    Raises:
      InvalidPhoneError: empty input, unparseable, or not a valid number.
    """
    if raw is None:
        raise InvalidPhoneError("phone is empty")
    s = str(raw).strip()
    if not s:
        raise InvalidPhoneError("phone is empty")
    if not any(c.isdigit() for c in s):
        raise InvalidPhoneError("phone must contain digits")

    # Ensure leading '+' so libphonenumber can detect the country code.
    digits_and_plus = re.sub(r"[^\d+]", "", s)
    if not digits_and_plus.startswith("+"):
        digits_and_plus = "+" + digits_and_plus

    try:
        parsed = phonenumbers.parse(digits_and_plus, None)
    except phonenumbers.NumberParseException as e:
        raise InvalidPhoneError(f"could not parse phone: {e}") from e

    if not phonenumbers.is_valid_number(parsed):
        raise InvalidPhoneError(
            "not a valid phone number — include DDI (e.g. 55 Brazil, 1 US)"
        )

    e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    return e164.lstrip("+")


# ── Contact model ──

@dataclass(frozen=True)
class Contact:
    id: str
    name: str
    phone_raw: str
    phone_uazapi: str
    status: str             # 'ativo' | 'inativo'
    created_at: datetime
    updated_at: datetime

    def is_active(self) -> bool:
        return self.status == "ativo"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat()
        d["updated_at"] = self.updated_at.isoformat()
        return d


# ── Repository ──

class ContactsRepo:
    def __init__(self, client=None):
        if client is not None:
            self.client = client
        else:
            from supabase import create_client
            url = os.environ.get("SUPABASE_URL")
            key = os.environ.get("SUPABASE_KEY")
            if not url or not key:
                raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set")
            self.client = create_client(url, key)

    # ---- Reads ----

    def list_active(self) -> list:
        """Contacts receiving broadcasts. Ordered by created_at desc."""
        resp = (
            self.client.table("contacts")
            .select("*")
            .eq("status", "ativo")
            .order("created_at", desc=True)
            .execute()
        )
        return [self._row_to_contact(r) for r in (resp.data or [])]

    def list_all(
        self,
        *,
        search: Optional[str] = None,
        page: int = 1,
        per_page: int = 10,
    ) -> tuple:
        """Paginated admin list, optional name search (ILIKE).
        Returns (rows_on_page, total_pages)."""
        import math
        q = self.client.table("contacts").select("*", count="exact")
        if search:
            q = q.ilike("name", f"%{search}%")
        start = (page - 1) * per_page
        end = start + per_page - 1
        resp = q.order("created_at", desc=True).range(start, end).execute()
        total = resp.count or 0
        total_pages = math.ceil(total / per_page) if total else 0
        return [self._row_to_contact(r) for r in (resp.data or [])], total_pages

    def get_by_phone(self, phone: str) -> "Contact":
        """Lookup by phone (accepts any format; normalizes internally)."""
        canonical = normalize_phone(phone)
        resp = (
            self.client.table("contacts")
            .select("*")
            .eq("phone_uazapi", canonical)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            raise ContactNotFoundError(f"no contact with phone {canonical}")
        return self._row_to_contact(rows[0])

    # ---- Internal ----

    @staticmethod
    def _row_to_contact(r: dict) -> "Contact":
        return Contact(
            id=r["id"],
            name=r["name"],
            phone_raw=r["phone_raw"],
            phone_uazapi=r["phone_uazapi"],
            status=r["status"],
            created_at=_parse_ts(r["created_at"]),
            updated_at=_parse_ts(r["updated_at"]),
        )


def _parse_ts(s: str) -> datetime:
    """Parse Supabase ISO timestamp (may end with 'Z' or '+00:00')."""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)
