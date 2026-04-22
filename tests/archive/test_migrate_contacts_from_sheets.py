"""Unit tests for the one-off Google Sheets → Supabase migration script."""
from unittest.mock import MagicMock, patch
import pytest

from scripts.migrate_contacts_from_sheets import (
    _pick_phone_raw,
    _normalize_for_migration,
    _row_to_payload,
)
from execution.integrations.contacts_repo import InvalidPhoneError


# ── _pick_phone_raw ──

def test_pick_phone_prefers_evolution_api():
    row = {"Evolution-api": "5511999", "n8n-evo": "x@s.whatsapp.net", "From": "whatsapp:+y"}
    assert _pick_phone_raw(row) == "5511999"


def test_pick_phone_falls_back_to_n8n_evo():
    row = {"Evolution-api": "", "n8n-evo": "5511888@s.whatsapp.net", "From": ""}
    assert _pick_phone_raw(row) == "5511888@s.whatsapp.net"


def test_pick_phone_falls_back_to_from_stripping_prefix():
    row = {"Evolution-api": "", "n8n-evo": "", "From": "whatsapp:+5511777"}
    assert _pick_phone_raw(row) == "+5511777"


def test_pick_phone_returns_none_when_all_empty():
    row = {"Evolution-api": "", "n8n-evo": "", "From": ""}
    assert _pick_phone_raw(row) is None


# ── _normalize_for_migration ──

def test_normalize_migration_preserves_explicit_ddi():
    assert _normalize_for_migration("+5511987654321") == "5511987654321"


def test_normalize_migration_br_fallback_11_digits():
    # BR mobile without DDI — 11 digits
    assert _normalize_for_migration("11987654321") == "5511987654321"


def test_normalize_migration_br_fallback_10_digits():
    # BR landline without DDI — 10 digits. Use a realistic one.
    result = _normalize_for_migration("1133334444")
    assert result.startswith("55")
    assert result.endswith("1133334444")


def test_normalize_migration_strips_whatsapp_prefix():
    assert _normalize_for_migration("whatsapp:+5511987654321") == "5511987654321"


def test_normalize_migration_strips_jid_suffix():
    assert _normalize_for_migration("5511987654321@s.whatsapp.net") == "5511987654321"


def test_normalize_migration_rejects_garbage():
    with pytest.raises(InvalidPhoneError):
        _normalize_for_migration("abc")


# ── _row_to_payload ──

def test_row_to_payload_active():
    row = {
        "ProfileName": "Alice",
        "Evolution-api": "5511987654321",
        "ButtonPayload": "Big",
    }
    payload = _row_to_payload(row)
    assert payload == {
        "name": "Alice",
        "phone_raw": "5511987654321",
        "phone_uazapi": "5511987654321",
        "status": "ativo",
    }


def test_row_to_payload_inactive():
    row = {
        "ProfileName": "Bob",
        "Evolution-api": "5511900000000",
        "ButtonPayload": "Inactive",
    }
    payload = _row_to_payload(row)
    assert payload["status"] == "inativo"


def test_row_to_payload_missing_button_payload_is_inactive():
    row = {"ProfileName": "Bob", "Evolution-api": "5511900000000"}
    payload = _row_to_payload(row)
    assert payload["status"] == "inativo"


def test_row_to_payload_returns_none_when_no_phone():
    row = {"ProfileName": "No Phone", "ButtonPayload": "Big"}
    assert _row_to_payload(row) is None


def test_row_to_payload_returns_none_when_no_name():
    row = {"ProfileName": "", "Evolution-api": "5511987654321"}
    assert _row_to_payload(row) is None


def test_row_to_payload_returns_none_on_invalid_phone():
    row = {"ProfileName": "Bad", "Evolution-api": "abc"}
    assert _row_to_payload(row) is None


# ── End-to-end integration of main() ──

def test_main_dry_run_does_not_upsert(capsys):
    fake_sheets = MagicMock()
    fake_sheets.list_contacts.return_value = (
        [
            {"ProfileName": "Alice", "Evolution-api": "5511987654321", "ButtonPayload": "Big"},
            {"ProfileName": "Bob",   "Evolution-api": "5511900000000", "ButtonPayload": "Inactive"},
        ],
        1,
    )
    fake_repo = MagicMock()
    fake_repo.client.table.return_value.upsert.return_value.execute.return_value.data = [{"id": "x"}]

    with patch("scripts.migrate_contacts_from_sheets.SheetsClient", return_value=fake_sheets), \
         patch("scripts.migrate_contacts_from_sheets.ContactsRepo", return_value=fake_repo):
        from scripts.migrate_contacts_from_sheets import main
        exit_code = main(["--dry-run"])

    assert exit_code == 0
    fake_repo.client.table.return_value.upsert.assert_not_called()
    out = capsys.readouterr().out
    assert "WOULD INSERT" in out


def test_main_real_run_calls_upsert():
    fake_sheets = MagicMock()
    fake_sheets.list_contacts.return_value = (
        [{"ProfileName": "Alice", "Evolution-api": "5511987654321", "ButtonPayload": "Big"}],
        1,
    )
    fake_repo = MagicMock()
    fake_repo.client.table.return_value.upsert.return_value.execute.return_value.data = [{"id": "x"}]

    with patch("scripts.migrate_contacts_from_sheets.SheetsClient", return_value=fake_sheets), \
         patch("scripts.migrate_contacts_from_sheets.ContactsRepo", return_value=fake_repo):
        from scripts.migrate_contacts_from_sheets import main
        exit_code = main([])

    assert exit_code == 0
    fake_repo.client.table.return_value.upsert.assert_called_once()
    call_kwargs = fake_repo.client.table.return_value.upsert.call_args.kwargs
    assert call_kwargs.get("on_conflict") == "phone_uazapi"
    assert call_kwargs.get("ignore_duplicates") is True


# ── Relaxed validation for historical BR phones ──

def test_normalize_migration_accepts_br_12_digit_pre2012_mobile():
    """12-digit BR mobile (pre-2012, no leading 9 after DDD) must be accepted."""
    # Example from the real sheet:
    assert _normalize_for_migration("553791000123") == "553791000123"


def test_normalize_migration_accepts_br_12_digit_via_evolution_api_format():
    """Same 12-digit number coming from Evolution-api column raw."""
    assert _normalize_for_migration("553798721100") == "553798721100"


def test_normalize_migration_still_rejects_true_garbage():
    with pytest.raises(InvalidPhoneError):
        _normalize_for_migration("abc")


def test_normalize_migration_still_rejects_empty():
    with pytest.raises(InvalidPhoneError):
        _normalize_for_migration("")
