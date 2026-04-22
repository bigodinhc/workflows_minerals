"""Tests for webhook.contact_admin module."""
import sys
from pathlib import Path
from datetime import datetime, timezone
import pytest

# Make webhook importable as a package root
sys.path.insert(0, str(Path(__file__).parent.parent / "webhook"))

from contact_admin import (
    parse_add_input,
    is_authorized,
    digits_only,
    start_add_flow,
    get_state,
    clear_state,
    is_awaiting_add,
    ADMIN_STATE,
    render_add_prompt,
    render_list_message,
    build_list_keyboard,
)
from execution.integrations.contacts_repo import Contact

_NOW = datetime.now(timezone.utc)


def _contact(id: str, name: str, phone: str, status: str = "ativo") -> Contact:
    """Helper: build a minimal Contact dataclass for keyboard tests."""
    return Contact(
        id=id,
        name=name,
        phone_raw=phone,
        phone_uazapi=phone,
        status=status,
        created_at=_NOW,
        updated_at=_NOW,
    )


# ── parse_add_input ──

def test_parse_add_input_valid():
    assert parse_add_input("Joao Silva 5511999999999") == ("Joao Silva", "5511999999999")


def test_parse_add_input_single_name():
    assert parse_add_input("Joao 5511999999999") == ("Joao", "5511999999999")


def test_parse_add_input_multiword_name():
    assert parse_add_input("Ana Maria Santos 5511999999999") == ("Ana Maria Santos", "5511999999999")


def test_parse_add_input_strips_phone_prefixes():
    name, phone = parse_add_input("Joao +5511999999999")
    assert phone == "5511999999999"


def test_parse_add_input_strips_whatsapp_jid():
    name, phone = parse_add_input("Joao 5511999999999@s.whatsapp.net")
    assert phone == "5511999999999"


def test_parse_add_input_missing_phone():
    with pytest.raises(ValueError, match="formato"):
        parse_add_input("Joao Silva")


def test_parse_add_input_empty():
    with pytest.raises(ValueError, match="formato"):
        parse_add_input("")


def test_parse_add_input_whitespace_only():
    with pytest.raises(ValueError, match="formato"):
        parse_add_input("   ")


def test_parse_add_input_rejects_non_digits_phone():
    with pytest.raises(ValueError, match="inv"):
        parse_add_input("Joao 5511abc9999")


def test_parse_add_input_rejects_short_phone():
    with pytest.raises(ValueError, match="curto"):
        parse_add_input("Joao 12345")


def test_parse_add_input_rejects_long_phone():
    with pytest.raises(ValueError, match="longo"):
        parse_add_input("Joao 12345678901234567")  # 17 digits


# ── digits_only ──

def test_digits_only_strips_formatting():
    assert digits_only("+55 (11) 99999-9999") == "5511999999999"


def test_digits_only_handles_empty():
    assert digits_only("") == ""


# ── is_authorized ──

def test_is_authorized_matches_admin(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    assert is_authorized(12345) is True
    assert is_authorized("12345") is True


def test_is_authorized_rejects_other(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    assert is_authorized(99999) is False


def test_is_authorized_rejects_when_env_missing(monkeypatch):
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert is_authorized(12345) is False


# ── State machine helpers ──

def test_start_add_flow_sets_state():
    ADMIN_STATE.clear()
    start_add_flow(123)
    assert is_awaiting_add(123) is True


def test_clear_state_removes_entry():
    ADMIN_STATE.clear()
    start_add_flow(123)
    clear_state(123)
    assert is_awaiting_add(123) is False


def test_clear_state_on_missing_chat_is_noop():
    ADMIN_STATE.clear()
    clear_state(999)  # no raise
    assert is_awaiting_add(999) is False


def test_is_awaiting_add_false_when_no_state():
    ADMIN_STATE.clear()
    assert is_awaiting_add(123) is False


def test_expired_state_treated_as_not_awaiting():
    from datetime import datetime, timedelta
    ADMIN_STATE.clear()
    ADMIN_STATE[123] = {
        "awaiting": "add_data",
        "expires_at": datetime.now() - timedelta(minutes=1),
    }
    assert is_awaiting_add(123) is False


def test_expired_state_cleaned_up_on_check():
    from datetime import datetime, timedelta
    ADMIN_STATE.clear()
    ADMIN_STATE[123] = {
        "awaiting": "add_data",
        "expires_at": datetime.now() - timedelta(minutes=1),
    }
    is_awaiting_add(123)  # triggers cleanup
    assert 123 not in ADMIN_STATE


def test_start_add_flow_overwrites_existing_state():
    ADMIN_STATE.clear()
    start_add_flow(123)
    first_expiry = ADMIN_STATE[123]["expires_at"]
    # Start again
    start_add_flow(123)
    # Expiry should be reset to a time >= the previous expiry
    assert ADMIN_STATE[123]["expires_at"] >= first_expiry


# ── render_add_prompt ──

def test_render_add_prompt_has_format_and_example():
    msg = render_add_prompt()
    assert "Nome Telefone" in msg
    assert "Exemplo" in msg
    assert "/cancel" in msg


# ── render_list_message ──

def test_render_list_message_with_contacts():
    contacts = [
        {"ProfileName": "A", "From": "whatsapp:+111", "ButtonPayload": "Big"},
        {"ProfileName": "B", "From": "whatsapp:+222", "ButtonPayload": "Inactive"},
    ]
    msg = render_list_message(contacts, total=25, page=2, per_page=10, search=None)
    assert "25" in msg  # total shown
    assert "Página 2" in msg or "Pagina 2" in msg


def test_render_list_message_with_search():
    contacts = [{"ProfileName": "Joao", "From": "whatsapp:+111", "ButtonPayload": "Big"}]
    msg = render_list_message(contacts, total=1, page=1, per_page=10, search="joao")
    assert "joao" in msg.lower()


def test_render_list_message_empty_with_search():
    msg = render_list_message([], total=0, page=1, per_page=10, search="xyz")
    assert "xyz" in msg
    assert "Nenhum" in msg or "nenhum" in msg.lower()


def test_render_list_message_empty_without_search():
    msg = render_list_message([], total=0, page=1, per_page=10, search=None)
    assert "/add" in msg


# ── build_list_keyboard ──
# Contacts are now Contact dataclass instances (not dicts).
# status "ativo" -> checkmark, "inativo" -> cross.
# Layout: N contact rows, optional nav row (when total_pages > 1), bulk-action row.

def test_build_list_keyboard_has_toggle_buttons():
    contacts = [
        _contact("a", "A", "5511111", status="ativo"),
        _contact("b", "B", "5511222", status="inativo"),
    ]
    kb = build_list_keyboard(contacts, page=1, total_pages=1, search=None)
    rows = kb["inline_keyboard"]
    # First 2 rows = contact toggles
    assert rows[0][0]["callback_data"] == "tgl:5511111"
    assert "✅" in rows[0][0]["text"]  # ativo = active
    assert "A" in rows[0][0]["text"]
    assert rows[1][0]["callback_data"] == "tgl:5511222"
    assert "❌" in rows[1][0]["text"]  # inativo


def test_build_list_keyboard_includes_nav_when_multiple_pages():
    contacts = [_contact("a", "A", "111", status="ativo")]
    kb = build_list_keyboard(contacts, page=2, total_pages=5, search=None)
    rows = kb["inline_keyboard"]
    # Row order: contact, nav, bulk. Nav is second-to-last.
    nav = rows[-2]
    callbacks = [b["callback_data"] for b in nav]
    # ContactPage.pack() emits trailing ':' for empty search field
    assert "pg:1:" in callbacks  # prev
    assert "pg:3:" in callbacks  # next
    assert "nop" in callbacks  # center indicator


def test_build_list_keyboard_nav_with_search():
    contacts = [_contact("a", "A", "111", status="ativo")]
    kb = build_list_keyboard(contacts, page=2, total_pages=3, search="joao")
    # nav is second-to-last; last is bulk row
    nav = kb["inline_keyboard"][-2]
    callbacks = [b["callback_data"] for b in nav]
    assert "pg:1:joao" in callbacks
    assert "pg:3:joao" in callbacks


def test_build_list_keyboard_no_nav_when_single_page():
    contacts = [_contact("a", "A", "111", status="ativo")]
    kb = build_list_keyboard(contacts, page=1, total_pages=1, search=None)
    # No nav row when total_pages == 1.
    # Rows: 1 contact toggle + 1 bulk-action row = 2 total; no pg: callback.
    callbacks = [btn["callback_data"] for row in kb["inline_keyboard"] for btn in row]
    assert not any(c.startswith("pg:") for c in callbacks), "nav row should be absent"


def test_build_list_keyboard_empty_contacts():
    kb = build_list_keyboard([], page=1, total_pages=0, search=None)
    # Empty keyboard is OK (caller uses render message instead)
    assert kb["inline_keyboard"] == []
