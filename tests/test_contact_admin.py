"""Tests for webhook.contact_admin module."""
import sys
from pathlib import Path
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
