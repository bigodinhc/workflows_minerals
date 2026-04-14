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


# ── Message formatting ──

def render_add_prompt() -> str:
    """Message shown when user types /add."""
    return (
        "📝 *ADICIONAR CONTATO*\n\n"
        "Envie no formato:\n"
        "`Nome Telefone`\n\n"
        "Exemplo: `Joao Silva 5511999999999`\n\n"
        "Use /cancel pra desistir."
    )


def render_list_message(contacts: list, total: int, page: int, per_page: int,
                        search: Optional[str]) -> str:
    """Message text for /list. Renders the header — contacts go in keyboard buttons."""
    if not contacts:
        if search:
            return f"📋 Nenhum contato encontrado pra \"{search}\""
        return "📋 Nenhum contato cadastrado. Use /add"

    import math
    total_pages = math.ceil(total / per_page) if per_page else 1

    if search:
        header = f"📋 *RESULTADO BUSCA* \"{search}\" ({total})"
    else:
        header = f"📋 *CONTATOS* ({total}) — Página {page}/{total_pages}"

    return header + "\n\nToque pra ativar/desativar."


def build_list_keyboard(contacts: list, page: int, total_pages: int,
                        search: Optional[str]) -> dict:
    """
    Build inline_keyboard dict with one toggle button per contact,
    plus a bottom nav row if total_pages > 1.
    """
    rows = []

    for c in contacts:
        name = c.get("ProfileName", "—")
        raw_phone = (
            c.get("Evolution-api")
            or c.get("n8n-evo")
            or c.get("From")
            or ""
        )
        phone_digits = digits_only(str(raw_phone))
        status = str(c.get("ButtonPayload", "")).strip()
        emoji = "✅" if status == "Big" else "❌"
        label = f"{emoji} {name} — {phone_digits}"
        # Telegram button text limit: 64 chars
        if len(label) > 60:
            label = label[:57] + "..."
        rows.append([{
            "text": label,
            "callback_data": f"tgl:{phone_digits}",
        }])

    # Navigation row (only when >1 page)
    if total_pages > 1:
        prev_page = max(1, page - 1)
        next_page = min(total_pages, page + 1)
        suffix = f":{search}" if search else ""
        rows.append([
            {"text": "◀", "callback_data": f"pg:{prev_page}{suffix}"},
            {"text": f"{page}/{total_pages}", "callback_data": "nop"},
            {"text": "▶", "callback_data": f"pg:{next_page}{suffix}"},
        ])

    return {"inline_keyboard": rows}
