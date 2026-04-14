"""
Contact admin: parsing, authorization, formatting, and state management
for /add and /list commands in the Telegram bot.
"""
import os
from datetime import datetime, timedelta
from typing import Optional, Tuple


# ── State ──

ADMIN_STATE: dict = {}  # chat_id (int or str) → {"awaiting": str, "expires_at": datetime}
STATE_TTL = timedelta(minutes=5)


# ── Helpers ──

def digits_only(s: str) -> str:
    """Return only digits from a string."""
    return "".join(c for c in str(s) if c.isdigit())


# ── Authorization ──

def is_authorized(chat_id) -> bool:
    """Check whether chat_id matches the admin TELEGRAM_CHAT_ID env var."""
    admin_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not admin_id:
        return False
    return str(chat_id) == admin_id


# ── Parsers ──

def parse_add_input(text: str) -> Tuple[str, str]:
    """
    Parse '<Nome ...> <phone>' into (name, phone).
    Phone must be the last whitespace-separated token.
    Raises ValueError with user-friendly message on bad input.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("formato inválido. Envie: Nome Telefone")

    parts = text.rsplit(None, 1)
    if len(parts) < 2:
        raise ValueError("formato inválido. Envie: Nome Telefone")

    name_raw, phone_raw = parts
    name = name_raw.strip()
    if not name:
        raise ValueError("formato inválido. Envie: Nome Telefone")

    # If the last token has no digits at all it's not a phone — bad format
    if not any(c.isdigit() for c in phone_raw):
        raise ValueError("formato inválido. Envie: Nome Telefone")

    phone_digits = digits_only(phone_raw)
    # Reject if phone_raw had unexpected characters
    allowed_chars = set("+0123456789 -().@swhatpne")
    for ch in phone_raw:
        if ch not in allowed_chars:
            raise ValueError(f"Telefone inválido. Só dígitos, ex: 5511999999999")

    if not phone_digits:
        raise ValueError("Telefone inválido. Só dígitos, ex: 5511999999999")

    if len(phone_digits) < 10:
        raise ValueError("Telefone muito curto (mínimo 10 dígitos)")

    if len(phone_digits) > 15:
        raise ValueError("Telefone muito longo (máximo 15 dígitos)")

    return (name, phone_digits)


# ── State Helpers ──

def start_add_flow(chat_id) -> None:
    """Mark chat as awaiting add data. Resets TTL."""
    ADMIN_STATE[chat_id] = {
        "awaiting": "add_data",
        "expires_at": datetime.now() + STATE_TTL,
    }


def get_state(chat_id) -> Optional[dict]:
    """Return state dict or None if absent/expired."""
    state = ADMIN_STATE.get(chat_id)
    if state is None:
        return None
    if state.get("expires_at") and state["expires_at"] < datetime.now():
        ADMIN_STATE.pop(chat_id, None)
        return None
    return state


def clear_state(chat_id) -> None:
    """Remove state for chat_id if present. No-op otherwise."""
    ADMIN_STATE.pop(chat_id, None)


def is_awaiting_add(chat_id) -> bool:
    """True if chat_id is currently in add_data wait state (non-expired)."""
    state = get_state(chat_id)
    return state is not None and state.get("awaiting") == "add_data"
