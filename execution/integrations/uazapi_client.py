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
            "token": self.token
            # Não precisa Content-Type pois requests seta automaticamente para form
        }
        # Enviar como form data (não JSON) - igual ao n8n
        payload = {
            "number": str(number),
            "text": str(text)
        }
        
        try:
            # Usar data= para form data (igual ao n8n)
            response = requests.post(url, headers=headers, data=payload, timeout=10)
            
            # Debug: print response for 4xx errors
            if response.status_code >= 400:
                print(f"[DEBUG] Response Status: {response.status_code}")
                print(f"[DEBUG] Response Body: {response.text[:500]}")
            
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.logger.error(f"Failed to send to {number}", {"error": str(e)})
            raise e
