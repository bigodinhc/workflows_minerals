"""Unit tests for normalize_phone (pure, no Supabase)."""
import pytest
from execution.integrations.contacts_repo import normalize_phone, InvalidPhoneError


def test_normalize_e164_brazilian_mobile():
    assert normalize_phone("+55 (11) 98765-4321") == "5511987654321"


def test_normalize_plain_digits_with_ddi():
    assert normalize_phone("5511987654321") == "5511987654321"


def test_normalize_idempotent():
    canonical = normalize_phone("+5511987654321")
    assert normalize_phone(canonical) == canonical


def test_normalize_us_number():
    assert normalize_phone("+1 415-555-2671") == "14155552671"


def test_normalize_rejects_empty():
    with pytest.raises(InvalidPhoneError):
        normalize_phone("")


def test_normalize_rejects_none():
    with pytest.raises(InvalidPhoneError):
        normalize_phone(None)


def test_normalize_rejects_letters_only():
    with pytest.raises(InvalidPhoneError):
        normalize_phone("abc")


def test_normalize_rejects_too_short():
    with pytest.raises(InvalidPhoneError):
        normalize_phone("12345")


def test_normalize_rejects_invalid_number_for_country():
    # country code 55 (BR) with obviously invalid national number
    with pytest.raises(InvalidPhoneError):
        normalize_phone("+5500000000000")


def test_normalize_preserves_plus_stripped_from_output():
    result = normalize_phone("+5511987654321")
    assert "+" not in result
    assert result.isdigit()
