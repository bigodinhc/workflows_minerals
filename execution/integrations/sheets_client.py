import os
import gspread
import base64
import json
from ..core.logger import WorkflowLogger

class SheetsClient:
    def __init__(self):
        # GH Actions stores multiline JSON secrets as base64 often or just string
        # We try to handle both cases: file path or env var content
        creds_path = os.environ.get("GOOGLE_CREDENTIALS_PATH", "credentials.json")
        creds_content = os.environ.get("GOOGLE_CREDENTIALS_JSON")
        
        try:
            if creds_content:
                # If env var provided (likely base64 encoded for safety in GH Actions)
                try:
                    decoded = base64.b64decode(creds_content).decode('utf-8')
                    creds_dict = json.loads(decoded)
                    self.gc = gspread.service_account_from_dict(creds_dict)
                except:
                    # Maybe it wasn't base64?
                    creds_dict = json.loads(creds_content)
                    self.gc = gspread.service_account_from_dict(creds_dict)
            else:
                # Fallback to file
                self.gc = gspread.service_account(filename=creds_path)
                
            self.logger = WorkflowLogger("SheetsClient")
            
        except Exception as e:
            raise ValueError(f"Failed to authenticate Google Sheets: {str(e)}")

    def get_contacts(self, sheet_id, sheet_name="Página1"):
        """
        Get contacts from Google Sheets.
        Filters for ButtonPayload == 'Big'
        """
        try:
            sh = self.gc.open_by_key(sheet_id)
            worksheet = sh.worksheet(sheet_name)
            records = worksheet.get_all_records()
            
            # Filter in Python (easier/cheaper than Sheets API filter views)
            valid_contacts = [
                r for r in records 
                if str(r.get("ButtonPayload", "")).strip() == "Big"
            ]
            
            self.logger.info(f"Found {len(valid_contacts)} valid contacts out of {len(records)}")
            return valid_contacts
            
        except Exception as e:
            self.logger.error("Failed to fetch contacts", {"error": str(e)})
            raise e
