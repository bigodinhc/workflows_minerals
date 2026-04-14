"""Tests for SheetsClient contact management operations."""
from unittest.mock import MagicMock, patch
import pytest
from execution.integrations.sheets_client import SheetsClient


@pytest.fixture
def mock_sheets_client():
    """Return a SheetsClient with internals mocked."""
    with patch("execution.integrations.sheets_client.gspread"):
        client = SheetsClient.__new__(SheetsClient)
        client.gc = MagicMock()
        client.logger = MagicMock()
        return client


def _mk_rows(active_count, inactive_count):
    """Helper to build mock sheet rows."""
    rows = []
    for i in range(active_count):
        rows.append({
            "ProfileName": f"Active{i}",
            "From": f"whatsapp:+55119{i:08d}",
            "n8n-evo": f"55119{i:08d}@s.whatsapp.net",
            "ButtonPayload": "Big",
        })
    for i in range(inactive_count):
        rows.append({
            "ProfileName": f"Inactive{i}",
            "From": f"whatsapp:+55219{i:08d}",
            "n8n-evo": f"55219{i:08d}@s.whatsapp.net",
            "ButtonPayload": "Inactive",
        })
    return rows


def test_list_returns_first_page(mock_sheets_client):
    rows = _mk_rows(25, 0)
    worksheet = MagicMock()
    worksheet.get_all_records.return_value = rows
    mock_sheets_client.gc.open_by_key.return_value.worksheet.return_value = worksheet

    contacts, total_pages = mock_sheets_client.list_contacts("sheet_id", page=1, per_page=10)
    assert len(contacts) == 10
    assert contacts[0]["ProfileName"] == "Active0"
    assert total_pages == 3  # ceil(25 / 10)


def test_list_returns_last_page_partial(mock_sheets_client):
    rows = _mk_rows(25, 0)
    worksheet = MagicMock()
    worksheet.get_all_records.return_value = rows
    mock_sheets_client.gc.open_by_key.return_value.worksheet.return_value = worksheet

    contacts, total_pages = mock_sheets_client.list_contacts("sheet_id", page=3, per_page=10)
    assert len(contacts) == 5
    assert contacts[0]["ProfileName"] == "Active20"


def test_list_search_case_insensitive(mock_sheets_client):
    rows = [
        {"ProfileName": "Joao Silva", "From": "a", "ButtonPayload": "Big"},
        {"ProfileName": "Maria", "From": "b", "ButtonPayload": "Big"},
        {"ProfileName": "JOAO Pedro", "From": "c", "ButtonPayload": "Inactive"},
        {"ProfileName": "Carlos", "From": "d", "ButtonPayload": "Big"},
    ]
    worksheet = MagicMock()
    worksheet.get_all_records.return_value = rows
    mock_sheets_client.gc.open_by_key.return_value.worksheet.return_value = worksheet

    contacts, total_pages = mock_sheets_client.list_contacts("sheet_id", search="joao")
    assert len(contacts) == 2
    names = {c["ProfileName"] for c in contacts}
    assert names == {"Joao Silva", "JOAO Pedro"}


def test_list_search_no_matches(mock_sheets_client):
    rows = _mk_rows(5, 0)
    worksheet = MagicMock()
    worksheet.get_all_records.return_value = rows
    mock_sheets_client.gc.open_by_key.return_value.worksheet.return_value = worksheet

    contacts, total_pages = mock_sheets_client.list_contacts("sheet_id", search="xyz")
    assert contacts == []
    assert total_pages == 0


def test_list_empty_sheet(mock_sheets_client):
    worksheet = MagicMock()
    worksheet.get_all_records.return_value = []
    mock_sheets_client.gc.open_by_key.return_value.worksheet.return_value = worksheet

    contacts, total_pages = mock_sheets_client.list_contacts("sheet_id")
    assert contacts == []
    assert total_pages == 0


def test_list_includes_both_active_and_inactive(mock_sheets_client):
    """list_contacts should NOT filter by ButtonPayload — admin sees all."""
    rows = _mk_rows(3, 2)
    worksheet = MagicMock()
    worksheet.get_all_records.return_value = rows
    mock_sheets_client.gc.open_by_key.return_value.worksheet.return_value = worksheet

    contacts, total_pages = mock_sheets_client.list_contacts("sheet_id", per_page=100)
    assert len(contacts) == 5
