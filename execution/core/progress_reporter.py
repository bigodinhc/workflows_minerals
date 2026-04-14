"""
Progress reporter: sends one Telegram message at workflow start and edits it
throughout the run. Designed to be used alongside DeliveryReporter (pass
on_progress=progress.on_dispatch_tick and notify_telegram=False).

All methods are non-raising. Telegram failures degrade to log warnings so
the workflow is never broken by a notification failure.
"""
from datetime import datetime
from typing import Optional


class ProgressReporter:
    def __init__(
        self,
        workflow: str,
        chat_id: Optional[str] = None,
        dashboard_base_url: str = "https://workflows-minerals.vercel.app",
        gh_run_id: Optional[str] = None,
        telegram_client=None,
    ):
        self.workflow = workflow
        self.chat_id = chat_id
        self.dashboard_base_url = dashboard_base_url
        self.gh_run_id = gh_run_id
        self._telegram_client = telegram_client
        self._message_id: Optional[int] = None
        self._disabled: bool = False
        self._last_edit_at: float = 0.0
        self._last_edit_pct: int = 0
        self._started_at: Optional[datetime] = None

    def _get_client(self):
        if self._telegram_client is not None:
            return self._telegram_client
        from execution.integrations.telegram_client import TelegramClient
        self._telegram_client = TelegramClient()
        return self._telegram_client

    def _header(self, emoji: str, body: str) -> str:
        title = self.workflow.upper().replace("_", " ")
        started = self._started_at or datetime.now().astimezone()
        when = started.strftime("%d/%m/%Y %H:%M")
        return f"{emoji} {title}\n{when}\n{body}"

    def start(self, phase_text: str = "Preparando dados...") -> None:
        """Send initial message and store message_id. Never raises."""
        self._started_at = datetime.now().astimezone()
        text = self._header("⏳", phase_text)
        try:
            client = self._get_client()
            message_id = client.send_message(text=text, chat_id=self.chat_id)
        except Exception as exc:
            print(f"[WARN] ProgressReporter.start failed: {exc}")
            self._disabled = True
            return
        if message_id is None:
            self._disabled = True
            return
        self._message_id = message_id

    def update(self, text: str) -> None:
        """Edit the current message with new body text. Never raises."""
        if self._disabled or self._message_id is None:
            return
        full = self._header("⏳", text)
        try:
            client = self._get_client()
            client.edit_message_text(
                chat_id=self.chat_id,
                message_id=self._message_id,
                new_text=full,
            )
        except Exception as exc:
            print(f"[WARN] ProgressReporter.update failed: {exc}")
        import time as _time
        self._last_edit_at = _time.monotonic()
