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


def _setup_toggle_sheet(mock_client, rows):
    worksheet = MagicMock()
    worksheet.get_all_records.return_value = rows
    # headers: first row of sheet
    if rows:
        worksheet.row_values.return_value = list(rows[0].keys())
    else:
        worksheet.row_values.return_value = []
    mock_client.gc.open_by_key.return_value.worksheet.return_value = worksheet
    return worksheet


def test_toggle_big_to_inactive(mock_sheets_client):
    rows = [
        {"ProfileName": "Joao", "n8n-evo": "5511999@s.whatsapp.net",
         "From": "whatsapp:+5511999", "ButtonPayload": "Big"},
    ]
    ws = _setup_toggle_sheet(mock_sheets_client, rows)

    name, new_status = mock_sheets_client.toggle_contact("sheet_id", "5511999")
    assert name == "Joao"
    assert new_status == "Inactive"
    # Verify cell update called
    ws.update_cell.assert_called_once()
    call_args = ws.update_cell.call_args
    # update_cell(row, col, value) — row=2 (data row 1), col=idx of ButtonPayload+1
    expected_col = list(rows[0].keys()).index("ButtonPayload") + 1
    assert call_args[0][0] == 2  # row 2 (row 1 is header)
    assert call_args[0][1] == expected_col
    assert call_args[0][2] == "Inactive"


def test_toggle_inactive_to_big(mock_sheets_client):
    rows = [
        {"ProfileName": "Joao", "n8n-evo": "5511999@s.whatsapp.net",
         "From": "whatsapp:+5511999", "ButtonPayload": "Inactive"},
    ]
    ws = _setup_toggle_sheet(mock_sheets_client, rows)

    name, new_status = mock_sheets_client.toggle_contact("sheet_id", "5511999")
    assert name == "Joao"
    assert new_status == "Big"
    ws.update_cell.assert_called_once()
    assert ws.update_cell.call_args[0][2] == "Big"


def test_toggle_matches_phone_by_digits_only(mock_sheets_client):
    """Phone lookup should match by digits, ignoring format differences."""
    rows = [
        {"ProfileName": "Joao",
         "From": "whatsapp:+5511999",
         "n8n-evo": "5511999@s.whatsapp.net",
         "ButtonPayload": "Big"},
    ]
    ws = _setup_toggle_sheet(mock_sheets_client, rows)

    # Phone arg has different formatting — should still match
    name, _ = mock_sheets_client.toggle_contact("sheet_id", "+5511999")
    assert name == "Joao"


def test_toggle_raises_when_phone_not_found(mock_sheets_client):
    rows = [
        {"ProfileName": "Joao", "From": "whatsapp:+5511999",
         "n8n-evo": "5511999@s.whatsapp.net", "ButtonPayload": "Big"},
    ]
    _setup_toggle_sheet(mock_sheets_client, rows)

    with pytest.raises(ValueError, match="not found"):
        mock_sheets_client.toggle_contact("sheet_id", "99999")


def test_add_contact_appends_row_with_defaults(mock_sheets_client):
    rows = [
        {"ProfileName": "Existing", "MessageType": "button",
         "SmsStatus": "received", "Body": "Sim, quero receber",
         "From": "whatsapp:+5511888", "ButtonPayload": "Big",
         "To": "whatsapp:+5511000000000",
         "n8n-evo": "5511888@s.whatsapp.net"},
    ]
    ws = _setup_toggle_sheet(mock_sheets_client, rows)

    mock_sheets_client.add_contact("sheet_id", "Joao Silva", "5511999999999")

    ws.append_row.assert_called_once()
    appended = ws.append_row.call_args[0][0]
    # appended is a list matching headers order
    headers = list(rows[0].keys())
    row_dict = dict(zip(headers, appended))
    assert row_dict["ProfileName"] == "Joao Silva"
    assert row_dict["MessageType"] == "button"
    assert row_dict["SmsStatus"] == "received"
    assert row_dict["Body"] == "Sim, quero receber (via bot)"
    assert row_dict["From"] == "whatsapp:+5511999999999"
    assert row_dict["ButtonPayload"] == "Big"
    assert row_dict["To"] == "whatsapp:+5511000000000"  # copied from last row
    assert row_dict["n8n-evo"] == "5511999999999@s.whatsapp.net"


def test_add_contact_raises_on_duplicate_active(mock_sheets_client):
    rows = [
        {"ProfileName": "Joao", "From": "whatsapp:+5511999",
         "n8n-evo": "5511999@s.whatsapp.net", "ButtonPayload": "Big"},
    ]
    _setup_toggle_sheet(mock_sheets_client, rows)

    with pytest.raises(ValueError, match="already exists"):
        mock_sheets_client.add_contact("sheet_id", "Novo", "5511999")


def test_add_contact_raises_on_duplicate_inactive(mock_sheets_client):
    rows = [
        {"ProfileName": "Joao", "From": "whatsapp:+5511999",
         "n8n-evo": "5511999@s.whatsapp.net", "ButtonPayload": "Inactive"},
    ]
    _setup_toggle_sheet(mock_sheets_client, rows)

    with pytest.raises(ValueError, match="already exists"):
        mock_sheets_client.add_contact("sheet_id", "Novo", "5511999")


def test_add_contact_empty_sheet_uses_empty_to(mock_sheets_client):
    """When sheet is empty, 'To' field defaults to empty string (no last row to copy)."""
    ws = MagicMock()
    ws.get_all_records.return_value = []
    ws.row_values.return_value = ["ProfileName", "From", "ButtonPayload", "To"]
    mock_sheets_client.gc.open_by_key.return_value.worksheet.return_value = ws

    mock_sheets_client.add_contact("sheet_id", "Primeiro", "5511111")

    ws.append_row.assert_called_once()
    appended = ws.append_row.call_args[0][0]
    # Find index of "To" in headers
    to_idx = ws.row_values.return_value.index("To")
    assert appended[to_idx] == ""
