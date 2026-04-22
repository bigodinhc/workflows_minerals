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
