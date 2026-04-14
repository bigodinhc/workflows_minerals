import os
import gspread
import base64
import json
from ..core.logger import WorkflowLogger


def _digits_only(s: str) -> str:
    """Return only digits from a string."""
    return "".join(c for c in str(s) if c.isdigit())

class SheetsClient:
    def __init__(self):
        # Escopos explicitos garantem acesso
        SCOPES = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        
        self.logger = WorkflowLogger("SheetsClient")
        
        try:
            creds_content = os.environ.get("GOOGLE_CREDENTIALS_JSON")
            
            if not creds_content:
                # Fallback local file development
                creds_path = os.environ.get("GOOGLE_CREDENTIALS_PATH", "credentials.json")
                if os.path.exists(creds_path):
                    self.gc = gspread.service_account(filename=creds_path, scopes=SCOPES)
                else:
                    raise ValueError("GOOGLE_CREDENTIALS_JSON env var not missing")
            else:
                # Handle Env Var (JSON string)
                # Tenta primeiro JSON direto, se falhar tenta base64
                try:
                    creds_dict = json.loads(creds_content)
                except json.JSONDecodeError:
                    # Maybe base64?
                     decoded = base64.b64decode(creds_content).decode('utf-8')
                     creds_dict = json.loads(decoded)
                
                # DEBUG: Print the email we are using
                print(f"[DEBUG] Using Service Account Email: {creds_dict.get('client_email', 'UNKNOWN')}")
                
                # Create credentials object explicitly (More robust)
                from google.oauth2.service_account import Credentials
                creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
                self.gc = gspread.authorize(creds)
                
        except Exception as e:
            print(f"[ERROR] Auth failed: {str(e)}")
            raise ValueError(f"Failed to authenticate Google Sheets: {str(e)}")

    def get_contacts(self, sheet_id, sheet_name="Página1"):
        """
        Get contacts from Google Sheets.
        Filters for ButtonPayload == 'Big'
        """
        try:
            # Try to get service account email for debugging
            try:
                sa_email = self.gc.auth.service_account_email
                print(f"[DEBUG] Service Account Email: {sa_email}")
                print(f"[DEBUG] Opening sheet ID: {sheet_id}...")
            except:
                print("[DEBUG] Could not determine SA email")

            sh = self.gc.open_by_key(sheet_id)
            
            # List worksheets to help debug if name is wrong
            ws_names = [w.title for w in sh.worksheets()]
            print(f"[DEBUG] Available worksheets: {ws_names}")
            
            if sheet_name not in ws_names:
                # Fallback to first sheet if default fails
                print(f"[WARN] Sheet '{sheet_name}' not found. Using first sheet: '{ws_names[0]}'")
                worksheet = sh.sheet1
            else:
                worksheet = sh.worksheet(sheet_name)
            
            records = worksheet.get_all_records()
            print(f"[DEBUG] Total records found: {len(records)}")
            
            # Filter in Python
            valid_contacts = [
                r for r in records 
                if str(r.get("ButtonPayload", "")).strip() == "Big"
            ]
            
            self.logger.info(f"Found {len(valid_contacts)} valid contacts out of {len(records)}")
            return valid_contacts
            
        except Exception as e:
            # FORCE PRINT ERROR TO CONSOLE WITH TRACEBACK
            import traceback
            tb = traceback.format_exc()
            print(f"\n[ERROR] CRITICAL SHEETS FAILURE: {repr(e)}")
            print(f"[DEBUG] Traceback:\n{tb}")
            
            if "PERMISSION_DENIED" in str(e).upper():
                print(">>> HINT: Did you share the Google Sheet with the Service Account email?")
            
            self.logger.error("Failed to fetch contacts", {"error": str(e), "traceback": tb})
            raise e

    def list_contacts(
        self,
        sheet_id,
        sheet_name="Página1",
        search=None,
        page=1,
        per_page=10,
    ):
        """
        List all contacts (both active and inactive) for admin view.
        Optionally filtered by case-insensitive substring on ProfileName.
        Returns (contacts_on_page, total_pages).
        """
        import math
        try:
            sh = self.gc.open_by_key(sheet_id)
            try:
                worksheet = sh.worksheet(sheet_name)
            except gspread.WorksheetNotFound:
                worksheet = sh.sheet1

            records = worksheet.get_all_records()

            if search:
                needle = search.lower()
                records = [
                    r for r in records
                    if needle in str(r.get("ProfileName", "")).lower()
                ]

            total = len(records)
            if total == 0:
                return [], 0
            total_pages = math.ceil(total / per_page)
            start = (page - 1) * per_page
            end = start + per_page
            return records[start:end], total_pages
        except Exception as e:
            self.logger.error("list_contacts failed", {"error": str(e)})
            raise

    def toggle_contact(self, sheet_id, phone, sheet_name="Página1"):
        """
        Flip ButtonPayload between 'Big' and 'Inactive' for the row matching phone.
        Phone matching normalizes both sides to digits only.
        Returns (profile_name, new_status).
        Raises ValueError if not found.
        """
        try:
            sh = self.gc.open_by_key(sheet_id)
            try:
                worksheet = sh.worksheet(sheet_name)
            except Exception:
                worksheet = sh.sheet1

            headers = worksheet.row_values(1)
            if "ButtonPayload" not in headers:
                raise ValueError("ButtonPayload column not found in sheet")
            button_col_idx = headers.index("ButtonPayload") + 1  # 1-indexed

            needle = _digits_only(phone)
            records = worksheet.get_all_records()
            for i, row in enumerate(records):
                row_phone = (
                    row.get("Evolution-api")
                    or row.get("n8n-evo")
                    or row.get("From")
                    or ""
                )
                if _digits_only(str(row_phone)) == needle:
                    current = str(row.get("ButtonPayload", "")).strip()
                    new_status = "Inactive" if current == "Big" else "Big"
                    worksheet.update_cell(i + 2, button_col_idx, new_status)
                    return (row.get("ProfileName", "—"), new_status)

            raise ValueError(f"Contact with phone {phone} not found")
        except ValueError:
            raise
        except Exception as e:
            self.logger.error("toggle_contact failed", {"error": str(e)})
            raise

    def _get_or_create_control_sheet(self, sheet_id):
        """Helper to get Control sheet, creating if likely missing."""
        try:
            sh = self.gc.open_by_key(sheet_id)
            try:
                return sh.worksheet("Controle")
            except gspread.WorksheetNotFound:
                self.logger.info("Sheet 'Controle' not found. Creating...")
                # Create with header
                ws = sh.add_worksheet(title="Controle", rows=1000, cols=3)
                ws.append_row(["Date", "Type", "Status"])
                return ws
        except Exception as e:
            self.logger.error("Failed to get Control sheet", {"error": str(e)})
            raise e

    def check_daily_status(self, sheet_id, check_date: str, report_type: str) -> bool:
        """
        Check if a report was already sent today.
        Returns True if ALREADY SENT.
        """
        try:
            ws = self._get_or_create_control_sheet(sheet_id)
            records = ws.get_all_records()
            
            # Check for matching row
            for r in records:
                # Adjust column names to match what we created (Date, Type, Status)
                if str(r.get("Date")) == check_date and str(r.get("Type")) == report_type and str(r.get("Status")) == "SENT":
                    return True
            
            return False
        except Exception as e:
            self.logger.error("Error checking status", {"error": str(e)})
            return False # Fail safe: try to send if check fails? Or block? Let's say False to retry.

    def mark_daily_status(self, sheet_id, check_date: str, report_type: str):
        """Marks the report as sent for the day."""
        try:
            ws = self._get_or_create_control_sheet(sheet_id)
            ws.append_row([check_date, report_type, "SENT"])
            self.logger.info(f"Marked {report_type} as SENT for {check_date}")
        except Exception as e:
            self.logger.error("Error marking status", {"error": str(e)})
            # Don't raise, just log. Sending succeeded, marking failed.

