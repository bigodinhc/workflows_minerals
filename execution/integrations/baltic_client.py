
import os
import requests
import msal
from datetime import datetime, timedelta

class BalticClient:
    def __init__(self):
        self.tenant_id = os.getenv("AZURE_TENANT_ID")
        self.client_id = os.getenv("AZURE_CLIENT_ID")
        self.client_secret = os.getenv("AZURE_CLIENT_SECRET")
        self.authority = f"https://login.microsoftonline.com/{self.tenant_id}"
        self.scope = ["https://graph.microsoft.com/.default"]
        self.logger = None  # Can be injected

    def _get_token(self):
        app = msal.ConfidentialClientApplication(
            self.client_id,
            authority=self.authority,
            client_credential=self.client_secret
        )
        result = app.acquire_token_for_client(scopes=self.scope)
        if "access_token" in result:
            return result["access_token"]
        else:
            raise Exception(f"Failed to acquire token: {result.get('error_description')}")

    def find_latest_email(self, sender="DailyReports@midship.com", subject_keyword="Exchange"):
        """Finds the latest email matching criteria from the last 24h."""
        token = self._get_token()
        headers = {'Authorization': 'Bearer ' + token}
        
        # Look back 24 hours
        time_window = (datetime.utcnow() - timedelta(hours=24)).strftime('%Y-%m-%dT%H:%M:%SZ')
        
        # User ID or specific mailbox to check. 
        # For App Permissions, we check a specific USER mailbox usually.
        # User needs to provide TARGET_MAILBOX_ID (email address of the inbox).
        target_mailbox = os.getenv("AZURE_TARGET_MAILBOX")
        
        if not target_mailbox:
            raise ValueError("AZURE_TARGET_MAILBOX env var is required")

        url = f"https://graph.microsoft.com/v1.0/users/{target_mailbox}/messages"
        
        # OData filter - SIMPLIFIED to avoid InefficientFilter error
        # We only filter by Sender and Date, then check Subject in Python
        query_filter = (
            f"from/emailAddress/address eq '{sender}' and "
            f"receivedDateTime ge {time_window}"
        )
        
        # Debug URL
        print(f"DEBUG: Query Filter: {query_filter}")

        params = {
            "$filter": query_filter,
            "$orderby": "receivedDateTime desc",
            "$top": 10, # Fetch a few to find the right one
            "$select": "id,subject,receivedDateTime,hasAttachments"
        }
        
        response = requests.get(url, headers=headers, params=params)
        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            print(f"Graph API Error: {response.text}")
            raise e

        messages = response.json().get('value', [])
        
        # Client-side filtering for Subject
        for msg in messages:
            subject = msg.get("subject", "")
            if subject_keyword.lower() in subject.lower():
                return msg
                
        return None

    def get_pdf_attachment(self, message_id, target_mailbox=None):
        """Downloads the first PDF attachment from the message."""
        if not target_mailbox:
            target_mailbox = os.getenv("AZURE_TARGET_MAILBOX")

        token = self._get_token()
        headers = {'Authorization': 'Bearer ' + token}
        
        url = f"https://graph.microsoft.com/v1.0/users/{target_mailbox}/messages/{message_id}/attachments"
        
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        attachments = response.json().get('value', [])
        
        for att in attachments:
            name = att.get('name', '').lower()
            if name.endswith('.pdf') and att.get('@odata.type') == '#microsoft.graph.fileAttachment':
                # contentBytes is base64 encoded
                import base64
                return base64.b64decode(att['contentBytes']), att['name']
                
        return None, None
