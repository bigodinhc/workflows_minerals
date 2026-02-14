
import os
import requests
import json
from ..core.logger import WorkflowLogger

class TelegramClient:
    """
    Client for sending messages via Telegram Bot API.
    Supports inline keyboard buttons for approval workflows.
    """
    
    def __init__(self, token=None):
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN")
        if not self.token:
            raise ValueError("TELEGRAM_BOT_TOKEN not found")
        
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self.logger = WorkflowLogger("TelegramClient")
        
        # Default chat for notifications
        self.default_chat_id = os.getenv("TELEGRAM_CHAT_ID", "8375309778")
    
    def send_message(self, text, chat_id=None, reply_markup=None, parse_mode="Markdown"):
        """
        Send a text message.
        
        Args:
            text: Message text (supports Markdown)
            chat_id: Target chat ID (defaults to TELEGRAM_CHAT_ID env)
            reply_markup: Optional inline keyboard dict
            parse_mode: "Markdown" or "HTML"
        """
        chat_id = chat_id or self.default_chat_id
        
        payload = {
            "chat_id": chat_id,
            "text": text,
        }
        
        if parse_mode:
            payload["parse_mode"] = parse_mode
        
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
        
        try:
            response = requests.post(
                f"{self.base_url}/sendMessage",
                data=payload,
                timeout=10
            )
            response.raise_for_status()
            result = response.json()
            
            if result.get("ok"):
                self.logger.info(f"Message sent to {chat_id}")
                return result.get("result", {}).get("message_id")
            else:
                self.logger.error(f"Telegram API error: {result}")
                return None
                
        except Exception as e:
            self.logger.error(f"Failed to send message: {e}")
            raise
    
    def send_approval_request(self, draft_id, preview_text, chat_id=None):
        """
        Send a message with Approve/Reject inline buttons.
        
        Args:
            draft_id: Unique ID for the draft
            preview_text: Message preview to show
            chat_id: Target chat
        """
        # Build inline keyboard
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "✅ Aprovar e Enviar", "callback_data": f"approve:{draft_id}"},
                    {"text": "🧪 Teste", "callback_data": f"test_approve:{draft_id}"}
                ],
                [
                    {"text": "✏️ Ajustar", "callback_data": f"adjust:{draft_id}"},
                    {"text": "❌ Rejeitar", "callback_data": f"reject:{draft_id}"}
                ]
            ]
        }
        
        # Format message - no Markdown to avoid parsing errors with special chars
        header = "📰 RATIONALE NEWS - Aguardando Aprovação\n\n"
        footer = "\n\n---\nClique em um botão abaixo para processar."
        
        # Telegram limit is 4096 chars
        max_preview = 4096 - len(header) - len(footer) - 50
        truncated = preview_text[:max_preview]
        full_message = header + truncated + footer
        
        # Send without parse_mode to avoid Markdown errors
        return self.send_message(
            text=full_message,
            chat_id=chat_id,
            reply_markup=keyboard,
            parse_mode=None  # Plain text - avoids 400 errors from special chars
        )
    
    def answer_callback_query(self, callback_query_id, text="Processado!"):
        """
        Answer a callback query (button press acknowledgement).
        """
        try:
            response = requests.post(
                f"{self.base_url}/answerCallbackQuery",
                data={
                    "callback_query_id": callback_query_id,
                    "text": text
                },
                timeout=10
            )
            return response.json().get("ok", False)
        except Exception as e:
            self.logger.error(f"Failed to answer callback: {e}")
            return False
    
    def edit_message_text(self, chat_id, message_id, new_text, parse_mode="Markdown"):
        """
        Edit an existing message (e.g., to remove buttons after approval).
        """
        try:
            response = requests.post(
                f"{self.base_url}/editMessageText",
                data={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": new_text,
                    "parse_mode": parse_mode
                },
                timeout=10
            )
            return response.json().get("ok", False)
        except Exception as e:
            self.logger.error(f"Failed to edit message: {e}")
            return False
