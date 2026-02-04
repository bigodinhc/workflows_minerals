
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
        
        # OData filter - ONLY receivedDateTime (indexed property)
        # All other filtering done in Python to avoid InefficientFilter errors
        query_filter = f"receivedDateTime ge {time_window}"
        
        # Debug URL
        print(f"DEBUG: Query Filter: {query_filter}")

        params = {
            "$filter": query_filter,
            "$orderby": "receivedDateTime desc",
            "$top": 50, # Fetch more to find the right one
            "$select": "id,subject,receivedDateTime,hasAttachments,from"
        }
        
        response = requests.get(url, headers=headers, params=params)
        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            print(f"Graph API Error: {response.text}")
            raise e

        messages = response.json().get('value', [])
        
        # Client-side filtering for Sender and Subject
        for msg in messages:
            # Check sender
            from_addr = msg.get("from", {}).get("emailAddress", {}).get("address", "").lower()
            if sender.lower() not in from_addr:
                continue
            
            # Check subject
            subject = msg.get("subject", "")
            if subject_keyword.lower() in subject.lower():
                print(f"DEBUG: Found matching email: {subject}")
                return msg
                
        return None

    def get_pdf_attachment(self, message_id, target_mailbox=None):
        """Downloads the PDF - either from attachment or from link in HTML body."""
        if not target_mailbox:
            target_mailbox = os.getenv("AZURE_TARGET_MAILBOX")

        token = self._get_token()
        headers = {'Authorization': 'Bearer ' + token}
        
        # First try to get direct attachments
        att_url = f"https://graph.microsoft.com/v1.0/users/{target_mailbox}/messages/{message_id}/attachments"
        
        response = requests.get(att_url, headers=headers)
        response.raise_for_status()
        attachments = response.json().get('value', [])
        
        for att in attachments:
            name = att.get('name', '').lower()
            if name.endswith('.pdf') and att.get('@odata.type') == '#microsoft.graph.fileAttachment':
                # contentBytes is base64 encoded
                import base64
                print(f"DEBUG: Found PDF attachment: {att['name']}")
                return base64.b64decode(att['contentBytes']), att['name']
        
        # No direct attachment - try to extract PDF link from HTML body
        print("DEBUG: No PDF attachment, checking for PDF link in email body...")
        return self._get_pdf_from_body(message_id, target_mailbox, headers)
                
    def _get_pdf_from_body(self, message_id, target_mailbox, headers):
        """Extract PDF URL from email HTML body and download it."""
        import re
        
        # Get full email with body
        url = f"https://graph.microsoft.com/v1.0/users/{target_mailbox}/messages/{message_id}"
        params = {"$select": "body"}
        
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        
        body_content = response.json().get("body", {}).get("content", "")
        
        if not body_content:
            print("DEBUG: Email body is empty")
            return None, None
            
        # Pattern 1: Link with BDI text
        bdi_match = re.search(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>[^<]*BDI[^<]*</a>', body_content, re.I)
        if bdi_match:
            pdf_url = bdi_match.group(1)
            print(f"DEBUG: Found BDI link: {pdf_url}")
            return self._download_pdf_from_url(pdf_url)
            
        # Pattern 2: Direct PDF link
        pdf_match = re.search(r'<a[^>]+href=["\']([^"\']*(?:baltic|freight|index)[^"\']*\.pdf)["\']', body_content, re.I)
        if pdf_match:
            pdf_url = pdf_match.group(1)
            print(f"DEBUG: Found PDF link: {pdf_url}")
            return self._download_pdf_from_url(pdf_url)
            
        # Pattern 3: MID-SHIP tracking link
        tracking_match = re.search(r'<a[^>]+href=["\']([^"\']*midship[^"\']*)["\'][^>]*>.*?(?:CAPESIZE|BDI|INDEX)', body_content, re.I | re.DOTALL)
        if tracking_match:
            pdf_url = tracking_match.group(1)
            print(f"DEBUG: Found tracking link: {pdf_url}")
            return self._download_pdf_from_url(pdf_url)
            
        print("DEBUG: No PDF link found in email body")
        return None, None
        
    def _download_pdf_from_url(self, url):
        """Downloads PDF from a URL."""
        try:
            # Some links might be tracking redirects, follow them
            response = requests.get(url, allow_redirects=True, timeout=30)
            response.raise_for_status()
            
            # Check if we got a PDF
            content_type = response.headers.get('Content-Type', '')
            if 'pdf' in content_type.lower() or url.lower().endswith('.pdf'):
                filename = url.split('/')[-1].split('?')[0]
                if not filename.endswith('.pdf'):
                    filename = 'baltic_report.pdf'
                print(f"DEBUG: Downloaded PDF: {filename} ({len(response.content)} bytes)")
                return response.content, filename
            else:
                print(f"DEBUG: URL did not return PDF. Content-Type: {content_type}")
                return None, None
        except Exception as e:
            print(f"DEBUG: Failed to download PDF: {e}")
            return None, None

