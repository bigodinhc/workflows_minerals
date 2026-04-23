import os
import requests
import time
from ..core.logger import WorkflowLogger
from ..core.retry import retry_with_backoff

class UazapiClient:
    def __init__(self):
        # Fallback robusto: se env var vazia ou não definida, usa default
        self.base_url = os.environ.get("UAZAPI_URL") or "https://mineralstrading.uazapi.com"
        self.token = os.environ.get("UAZAPI_TOKEN")
        
        if not self.token:
            raise ValueError("UAZAPI_TOKEN must be set")
            
        self.logger = WorkflowLogger("UazapiClient")

    @retry_with_backoff(max_attempts=3, base_delay=2.0)
    def send_message(self, number, text):
        """
        Send text message via Uazapi.
        Includes rate limit handling (handled by caller mostly, but retry handles 500s)
        """
        url = f"{self.base_url}/send/text"
        headers = {
            "token": self.token,
            "Content-Type": "application/json"
        }
        # Enviar como JSON (confirmado na documentação oficial)
        payload = {
            "number": str(number),
            "text": str(text)
        }
        
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=10)
            
            # Debug: print response for 4xx errors
            if response.status_code >= 400:
                print(f"[DEBUG] Response Status: {response.status_code}")
                print(f"[DEBUG] Response Body: {response.text[:500]}")
            
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.logger.error(f"Failed to send to {number}", {"error": str(e)})
            raise e

    @retry_with_backoff(max_attempts=3, base_delay=2.0)
    def send_document(
        self,
        number: str,
        file_url: str,
        doc_name: str,
        caption: str = "",
    ) -> dict:
        """Send a document (PDF, etc.) via Uazapi /send/media.

        The `file_url` must be publicly fetchable by the Uazapi server —
        Graph `@microsoft.graph.downloadUrl` works because it's a pre-auth'd URL.
        """
        url = f"{self.base_url}/send/media"
        headers = {"token": self.token, "Content-Type": "application/json"}
        payload = {
            "number": str(number),
            "type": "document",
            "file": str(file_url),
            "docName": str(doc_name),
            "text": str(caption or ""),
        }
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            if response.status_code >= 400:
                self.logger.error(
                    f"send_document failed: {response.status_code} {response.text[:300]}"
                )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.logger.error(f"send_document to {number} failed", {"error": str(e)})
            raise
