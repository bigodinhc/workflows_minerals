"""
Progress reporter: sends one Telegram message at workflow start and edits it
throughout the run. Designed to be used alongside DeliveryReporter (pass
on_progress=progress.on_dispatch_tick and notify_telegram=False).

All methods are non-raising. Telegram failures degrade to log warnings so
the workflow is never broken by a notification failure.
"""
import time
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
        self._last_edit_at = time.monotonic()
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
        self._last_edit_at = time.monotonic()

    def on_dispatch_tick(self, processed: int, total: int, result) -> None:
        """Called once per DeliveryReporter progress event. Throttles edits.
        Edits when any of: (pct delta >= 10) OR (>=5s since last edit) OR
        (processed == total, force final).
        """
        if self._disabled or self._message_id is None or total <= 0:
            return
        now = time.monotonic()
        pct = int(processed * 100 / total)
        pct_delta = pct - self._last_edit_pct
        time_delta = now - self._last_edit_at
        is_final = processed == total

        should_edit = (pct_delta >= 10) or (time_delta >= 5.0) or is_final
        if not should_edit:
            return

        body = f"📤 Enviando pra {total} contatos... ({processed}/{total})"
        full = self._header("⏳", body)
        try:
            client = self._get_client()
            client.edit_message_text(
                chat_id=self.chat_id,
                message_id=self._message_id,
                new_text=full,
            )
        except Exception as exc:
            print(f"[WARN] ProgressReporter.on_dispatch_tick edit failed: {exc}")

        self._last_edit_at = now
        self._last_edit_pct = pct

    def finish(self, report) -> None:
        """Edit message with final summary. Reuses _format_telegram_message
        from delivery_reporter for format parity with today's notification.
        Never raises."""
        if self._disabled or self._message_id is None:
            return
        from execution.core.delivery_reporter import _format_telegram_message
        try:
            text = _format_telegram_message(
                report,
                dashboard_base_url=self.dashboard_base_url,
                gh_run_id=self.gh_run_id,
            )
        except Exception as exc:
            print(f"[WARN] ProgressReporter.finish format failed: {exc}")
            return
        try:
            client = self._get_client()
            client.edit_message_text(
                chat_id=self.chat_id,
                message_id=self._message_id,
                new_text=text,
            )
        except Exception as exc:
            print(f"[WARN] ProgressReporter.finish edit failed: {exc}")

    def finish_empty(self, reason: str) -> None:
        """Edit message to signal a no-op finish (e.g., no new articles)."""
        if self._disabled or self._message_id is None:
            return
        text = self._header("ℹ️", reason)
        try:
            client = self._get_client()
            client.edit_message_text(
                chat_id=self.chat_id,
                message_id=self._message_id,
                new_text=text,
            )
        except Exception as exc:
            print(f"[WARN] ProgressReporter.finish_empty edit failed: {exc}")
