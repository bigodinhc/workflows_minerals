
import os
import json
import base64
from anthropic import Anthropic

class ClaudeClient:
    def __init__(self):
        self.api_key = os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY env var is required")
        self.client = Anthropic(api_key=self.api_key)

    def extract_data_from_pdf(self, pdf_bytes):
        """Sends PDF bytes to Claude and returns extracted JSON data."""
        
        # Base64 encode for API
        pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")
        
        system_prompt = """You are an expert in extracting financial data from Baltic Exchange documents.
ANALYZE CAREFULLY ALL TABLES in the PDF.

Extract ALL data from the Routes tables, including:
- BCI (Baltic Capsize Index)
- C5TC (Timecharter Average)
- Routes C2, C3, C5, C7, C8, C9, C10, C14, C16, C17

Return ONLY a valid JSON in this format:
{
  "report_date": "YYYY-MM-DD",
  "bdi": {"value": 2017, "change": -29, "direction": "DOWN"},
  "capesize": {"value": 2884, "change": -105, "direction": "DOWN"},
  "panamax": {"value": 1874, "change": 0, "direction": "FLAT"},
  "supramax": {"value": 1461, "change": 14, "direction": "UP"},
  "handysize": {"value": 753, "change": 8, "direction": "UP"},
  "routes": [
    {"code": "BCI", "description": "Baltic Capsize Index", "value": 2884, "change": -105},
    {"code": "C5TC", "description": "Capesize Timecharter Average", "value": 23918, "change": -868, "unit": "USD/day"},
    {"code": "C2", "description": "Tubarao to Rotterdam", "type": "160,000 LT", "value": 11.7, "change": -0.186, "unit": "USD/ton"}
  ],
  "extraction_confidence": "high"
}

IMPORTANT:
- Use decimal numbers for freight values (e.g. 24.435)
- Use integers for indices (e.g. 2884)
- 'direction' should be UP, DOWN, or FLAT based on change
- If tables are not found, set extraction_confidence to "low"
"""

        message = self.client.messages.create(
            model="claude-3-5-sonnet-20240620",
            max_tokens=4096,
            system=system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": pdf_base64
                            }
                        },
                        {
                            "type": "text",
                            "text": "Extract all index and route data from this Baltic Exchange report."
                        }
                    ]
                }
            ]
        )
        
        # Parse response
        content = message.content[0].text
        
        # Cleanup markdown code blocks if present
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
            
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            print(f"Failed to parse JSON from Claude: {content}")
            return None
