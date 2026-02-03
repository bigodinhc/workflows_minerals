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
            # FORCE PRINT ERROR TO CONSOLE
            print(f"\n[ERROR] CRITICAL SHEETS FAILURE: {str(e)}")
            if "PERMISSION_DENIED" in str(e).upper():
                print(">>> HINT: Did you share the Google Sheet with the Service Account email?")
            self.logger.error("Failed to fetch contacts", {"error": str(e)})
            raise e
