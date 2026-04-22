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


def _normalize_phone_loose(raw) -> str:
    """Looser variant of normalize_phone: accepts any possible phone (passes
    phonenumbers.is_possible_number), not just fully-valid modern-format numbers.

    Needed to look up legacy pre-2012 BR mobiles (12 digits) that the migration
    preserved from the Google Sheet. The /add flow stays strict — this is only
    used as a fallback in get_by_phone.
    """
    if raw is None:
        raise InvalidPhoneError("phone is empty")
    s = str(raw).strip()
    if not s:
        raise InvalidPhoneError("phone is empty")
    digits_and_plus = re.sub(r"[^\d+]", "", s)
    if not digits_and_plus.startswith("+"):
        digits_and_plus = "+" + digits_and_plus
    try:
        parsed = phonenumbers.parse(digits_and_plus, None)
    except phonenumbers.NumberParseException as e:
        raise InvalidPhoneError(f"could not parse phone: {e}") from e
    if not phonenumbers.is_possible_number(parsed):
        raise InvalidPhoneError("phone length or country code is not possible")
    e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    return e164.lstrip("+")


def _br_sibling_forms(canonical: str) -> list:
    """For Brazilian mobiles, return the list of canonical forms that WhatsApp
    treats as equivalent: pre-2012 (12 digits, no leading 9 after DDD) and
    post-2012 (13 digits, mandatory 9 after DDD).

    Non-BR numbers return [canonical] unchanged. BR landlines (subscriber
    starts 2-5) also return unchanged — landlines never had the 9-prefix rule.
    """
    if not canonical.startswith("55"):
        return [canonical]
    # Post-2012 mobile: 55 + DDD(2) + "9" + 8digits = 13 total, position 4 == "9"
    if len(canonical) == 13 and canonical[4] == "9":
        without_9 = canonical[:4] + canonical[5:]  # drop the mandatory 9
        return [canonical, without_9]
    # Pre-2012 mobile: 55 + DDD(2) + 8digits starting 6-9 = 12 total
    if len(canonical) == 12 and canonical[4] in "6789":
        with_9 = canonical[:4] + "9" + canonical[4:]  # insert the modern 9
        return [canonical, with_9]
    return [canonical]


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
            # Accept SUPABASE_SERVICE_ROLE_KEY (webhook/Railway convention) or
            # SUPABASE_KEY (legacy script-side). Matches execution/core/event_bus.py.
            key = (
                os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
                or os.environ.get("SUPABASE_KEY")
            )
            if not url or not key:
                raise ValueError(
                    "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY "
                    "(or SUPABASE_KEY) must be set"
                )
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
        """Lookup by phone. Accepts strict (post-2012) format AND legacy
        pre-2012 BR format (12 digits, no leading 9 after DDD). For BR
        mobiles, searches both forms — WhatsApp treats them as equivalent,
        so callers shouldn't insert the same human twice."""
        try:
            canonical = normalize_phone(phone)
        except InvalidPhoneError:
            # Fall back to loose validation for legacy 12-digit BR numbers
            # that the migration preserved but normalize_phone rejects.
            canonical = _normalize_phone_loose(phone)
        candidates = _br_sibling_forms(canonical)
        resp = (
            self.client.table("contacts")
            .select("*")
            .in_("phone_uazapi", candidates)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            raise ContactNotFoundError(f"no contact with phone {canonical}")
        return self._row_to_contact(rows[0])

    # ---- Writes ----

    def add(
        self,
        name: str,
        phone_raw: str,
        *,
        send_welcome: Callable[[str], None],
    ) -> Contact:
        """Add a contact after validating the phone and dispatching a welcome
        message via the injected `send_welcome` callable.

        Flow: normalize → duplicate pre-check → send_welcome → insert.

        Raises:
          ValueError: if `name` is empty.
          InvalidPhoneError: if the phone cannot be normalized.
          ContactAlreadyExistsError: if phone_uazapi already present
            (pre-check or post-insert unique-violation race).
          RuntimeError: if `send_welcome` raises (wraps its exception).
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("name is empty")
        canonical = normalize_phone(phone_raw)

        # Duplicate pre-check — avoid sending welcome to someone on the list.
        try:
            existing = self.get_by_phone(canonical)
            raise ContactAlreadyExistsError(existing)
        except ContactNotFoundError:
            pass

        try:
            send_welcome(canonical)
        except Exception as e:
            raise RuntimeError(f"welcome send failed: {e}") from e

        # Insert. Unique index catches race conditions.
        try:
            resp = (
                self.client.table("contacts")
                .insert({
                    "name": name,
                    "phone_raw": phone_raw,
                    "phone_uazapi": canonical,
                    "status": "ativo",
                })
                .execute()
            )
        except Exception as e:
            if "duplicate key" in str(e).lower():
                existing = self.get_by_phone(canonical)
                raise ContactAlreadyExistsError(existing) from e
            raise
        return self._row_to_contact(resp.data[0])

    def toggle(self, phone: str) -> Contact:
        """Flip status ativo ↔ inativo. Raises ContactNotFoundError."""
        current = self.get_by_phone(phone)
        new_status = "inativo" if current.is_active() else "ativo"
        resp = (
            self.client.table("contacts")
            .update({"status": new_status})
            .eq("id", current.id)
            .execute()
        )
        return self._row_to_contact(resp.data[0])

    def bulk_set_status(
        self,
        status: str,
        *,
        search: Optional[str] = None,
    ) -> int:
        """Set status on all matching contacts. Returns count of rows updated.

        If `search` is None, affects ALL rows.
        If provided, affects only rows where name ILIKE %search%.
        """
        if status not in ("ativo", "inativo"):
            raise ValueError(f"invalid status: {status!r}")
        q = self.client.table("contacts").update({"status": status})
        if search:
            q = q.ilike("name", f"%{search}%")
        else:
            # postgrest update requires a filter; pick a tautology.
            q = q.neq("id", "00000000-0000-0000-0000-000000000000")
        resp = q.execute()
        return len(resp.data or [])

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
    """Parse Supabase ISO timestamp (may end with 'Z' or '+00:00').

    Python 3.9's datetime.fromisoformat requires microseconds to be exactly
    0, 3, or 6 digits. Supabase returns variable precision (e.g. 5 digits).
    Pad to 6 digits when needed.
    """
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    m = re.match(r"^(.*\.)(\d{1,5})([+-].*)$", s)
    if m:
        prefix, micro, tz = m.groups()
        s = prefix + micro.ljust(6, "0") + tz
    return datetime.fromisoformat(s)
