"""
Progress reporter: sends one Telegram message at workflow start and edits it
throughout the run. Designed to be used alongside DeliveryReporter (pass
on_progress=progress.on_dispatch_tick and notify_telegram=False).

All methods are non-raising. Telegram failures degrade to log warnings so
the workflow is never broken by a notification failure.
"""
import asyncio
import logging
import time
import time as _time
from datetime import datetime
from typing import Optional

try:
    from aiogram.exceptions import TelegramBadRequest
except ImportError:
    # Execution scripts may not have aiogram in import path — handle gracefully
    TelegramBadRequest = Exception  # type: ignore

_module_logger = logging.getLogger(__name__)

_TELEGRAM_MAX_CHARS = 4096
_PREVIEW_HEADER = "\n\n━━━━━━━━━━━━━━━━\n📝 *Mensagem enviada:*\n"
_TRUNCATION_SUFFIX = "\n...[truncada]"


def _append_message_preview(summary_text: str, message: str) -> str:
    """Append a message preview section, truncating to fit Telegram's 4096-char
    limit. If the combined text would overflow, the message body is cut and a
    suffix marker is added."""
    overhead = len(summary_text) + len(_PREVIEW_HEADER)
    available = _TELEGRAM_MAX_CHARS - overhead
    if available <= len(_TRUNCATION_SUFFIX):
        # Not enough room even for truncation marker: skip preview
        return summary_text
    if len(message) <= available:
        return summary_text + _PREVIEW_HEADER + message
    cut = available - len(_TRUNCATION_SUFFIX)
    return summary_text + _PREVIEW_HEADER + message[:cut] + _TRUNCATION_SUFFIX


class ProgressReporter:
    def __init__(
        self,
        workflow: str = "unknown",
        chat_id=None,
        dashboard_base_url: str = "https://workflows-minerals.vercel.app",
        gh_run_id: Optional[str] = None,
        telegram_client=None,
        # NEW params (all with defaults) — async/aiogram path:
        bot=None,
        run_id: Optional[str] = None,
        draft_id: Optional[str] = None,
        supabase_client=None,
    ):
        self.workflow = workflow
        self.chat_id = chat_id
        self.dashboard_base_url = dashboard_base_url
        self.gh_run_id = gh_run_id
        self._telegram_client = telegram_client
        self._message_id: Optional[int] = None
        self._disabled: bool = False
        self._last_edit_at: Optional[float] = None
        self._last_edit_pct: int = 0
        self._started_at: Optional[datetime] = None
        # Async/aiogram path
        self._bot = bot
        self.run_id = run_id
        self.draft_id = draft_id
        self._supabase = supabase_client
        self._pending_card_state: list = []
        self._flush_task: Optional[asyncio.Task] = None

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
        last = self._last_edit_at if self._last_edit_at is not None else 0.0
        time_delta = now - last
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

    # ── Async step() API ─────────────────────────────────────────────────────

    async def step(self, label: str, detail: str = "", level: str = "info") -> None:
        """Emit a progress step to three sinks: log, event_log, Telegram card."""
        await self._emit_structured_log(level, label, detail)
        # Fire-and-forget event_log persistence (doesn't block flow)
        asyncio.create_task(self._persist_event_log(level, label, detail))
        await self._update_telegram_card(label, detail, level)

    async def _emit_structured_log(self, level: str, label: str, detail: str) -> None:
        log_method = getattr(_module_logger, level, _module_logger.info)
        log_method(
            "progress.step",
            extra={
                "workflow": self.workflow,
                "run_id": self.run_id,
                "draft_id": self.draft_id,
                "label": label,
                "detail": detail,
            },
        )

    async def _persist_event_log(self, level: str, label: str, detail: str) -> None:
        if self._supabase is None:
            return
        try:
            self._supabase.table("event_log").insert({
                "workflow": self.workflow,
                "run_id": self.run_id,
                "draft_id": self.draft_id,
                "level": level,
                "label": label,
                "detail": detail,
            }).execute()
        except Exception as exc:
            _module_logger.warning("event_log_insert_failed: %s", exc)

    async def _update_telegram_card(self, label: str, detail: str, level: str) -> None:
        self._pending_card_state.append({"label": label, "detail": detail, "level": level})
        now = _time.monotonic()
        if self._last_edit_at is not None and (now - self._last_edit_at) < 2.0:
            # Debounce: schedule a flush if not already pending
            if self._flush_task is None or self._flush_task.done():
                self._flush_task = asyncio.create_task(self._delayed_flush())
            return
        await self._flush_now()

    async def _delayed_flush(self) -> None:
        await asyncio.sleep(2.0)
        await self._flush_now()

    async def _flush_now(self) -> None:
        """Render accumulated card state and edit the Telegram message."""
        if self._bot is None or self._message_id is None:
            return
        card_text = self._render_card()
        try:
            await self._bot.edit_message_text(
                card_text,
                chat_id=self.chat_id,
                message_id=self._message_id,
            )
        except TelegramBadRequest as e:
            msg = str(e).lower()
            if "message is not modified" not in msg:
                _module_logger.warning("progress_card_edit_failed: %s", e)
        except Exception as e:
            _module_logger.warning("progress_card_edit_unexpected: %s", e)
        self._last_edit_at = _time.monotonic()
        try:
            from metrics import progress_card_edits
            progress_card_edits.inc()
        except ImportError:
            pass

    def _render_card(self) -> str:
        """Compose the card body from accumulated steps.
        Glyphs: checkmark done, hourglass running (last), warning error."""
        lines = [f"📡 {self.workflow}", "━" * 22]
        for i, stp in enumerate(self._pending_card_state):
            is_last = (i == len(self._pending_card_state) - 1)
            if stp.get("level") == "error":
                glyph = "⚠️"
            elif is_last:
                glyph = "⏳"
            else:
                glyph = "✅"
            line = f"{glyph} {stp['label']}"
            if stp.get("detail"):
                line += f" — {stp['detail']}"
            lines.append(line)
        return "\n".join(lines)

    # ── Delivery-report finish (sync-compatible wrapper) ─────────────────────

    async def finish(self, report=None, message: Optional[str] = None) -> None:
        """Edit message with final summary. Reuses _format_telegram_message
        from delivery_reporter for format parity with today's notification.
        If message is provided, appends a preview of what was broadcast.
        Never raises.

        When `bot=` was passed to __init__, flushes the async card first.
        When only `telegram_client=` was passed, uses the sync telegram path.
        """
        # Async path: cancel pending debounce and flush card immediately
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
        if self._pending_card_state:
            await self._flush_now()

        if report is None:
            # Called from async path only — no delivery report to process
            return

        try:
            from execution.core import state_store
            summary = {
                "total": report.total,
                "success": report.success_count,
                "failure": report.failure_count,
            }
            duration_ms = int((report.finished_at - report.started_at).total_seconds() * 1000)
            if report.success_count > 0:
                state_store.record_success(self.workflow, summary, duration_ms)
            else:
                state_store.record_failure(self.workflow, summary, duration_ms)
        except Exception as exc:
            print(f"[WARN] ProgressReporter.finish state_store failed: {exc}")

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

        if message:
            text = _append_message_preview(text, message)

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
        try:
            from execution.core import state_store
            state_store.record_empty(self.workflow, reason)
        except Exception as exc:
            print(f"[WARN] ProgressReporter.finish_empty state_store failed: {exc}")

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

    def fail(self, exception: Exception) -> None:
        """Edit message with crash marker, push a distinct alert message,
        and record to state store. Called from outer try/except in script
        main(). Never raises."""
        exc_text = str(exception)[:200]

        # 1. Edit the existing card (as before)
        if not self._disabled and self._message_id is not None:
            text = self._header("🚨", f"CRASH: {exc_text}")
            try:
                client = self._get_client()
                client.edit_message_text(
                    chat_id=self.chat_id,
                    message_id=self._message_id,
                    new_text=text,
                )
            except Exception as e:
                print(f"[WARN] ProgressReporter.fail telegram edit failed: {e}")

        # 2. Push a distinct alert message so the operator gets notified
        try:
            client = self._get_client()
            alert_text = f"🚨 CRASH {self.workflow}: {exc_text[:120]}"
            client.send_message(text=alert_text, chat_id=self.chat_id)
        except Exception as e:
            print(f"[WARN] ProgressReporter.fail alert send failed: {e}")

        # 3. Record crash to state store (as before)
        try:
            from execution.core import state_store
            state_store.record_crash(self.workflow, exc_text)
        except Exception as e:
            print(f"[WARN] ProgressReporter.fail state_store failed: {e}")
