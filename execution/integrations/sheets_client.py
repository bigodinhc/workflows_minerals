import os
import gspread
import base64
import json
from ..core.logger import WorkflowLogger

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

