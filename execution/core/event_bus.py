"""
Event bus: single-point emitter for structured workflow events.

Fan-outs to multiple sinks (stdout, Supabase event_log, Sentry breadcrumbs,
main-chat Telegram for errors). Every sink is never-raise — failures are
logged to stderr/logger and swallowed so workflows are never broken by
telemetry.

Phase 1 (this module): stdout + Supabase + Sentry + main-chat sinks.
Phase 2 (later): _EventsChannelSink for firehose.
"""
import atexit
import functools
import json
import logging
import os
import secrets
import sys
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_VALID_LEVELS = frozenset({"info", "warn", "error"})


def _generate_run_id() -> str:
    """8-char hex, good enough for log grepping and far-from-collision."""
    return secrets.token_hex(4)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _monotonic() -> float:
    """Monkeypatch seam for tests that need to simulate time passage."""
    return time.monotonic()


def _get_supabase_client():
    """Return a supabase-py Client, or None if credentials/library missing.
    Extracted to module scope so tests can monkeypatch."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception as exc:
        logger.warning("supabase client init failed: %s", exc)
        return None


def _build_telegram_client():
    """Factory so tests can monkeypatch. Returns a TelegramClient or None on failure."""
    try:
        from execution.integrations.telegram_client import TelegramClient
        return TelegramClient()
    except Exception as exc:
        logger.warning("telegram client init failed: %s", exc)
        return None


_active_bus: ContextVar[Optional["EventBus"]] = ContextVar("active_event_bus", default=None)


def get_current_bus() -> Optional["EventBus"]:
    """Return the EventBus active for the current @with_event_bus context,
    or None if called outside a decorated function.

    Scripts and helpers use this to emit step/api_call events without
    threading the bus through call signatures. state_store.record_* uses
    it to tag last-run state with the event_bus run_id for /tail.

    Callers must tolerate None (outside decorator, or in tests)."""
    return _active_bus.get()


class EventBus:
    """Emit structured events to multiple sinks. Never raises."""

    def __init__(
        self,
        workflow: str,
        run_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        parent_run_id: Optional[str] = None,
    ):
        self.workflow = workflow
        self.run_id = run_id or _generate_run_id()
        self.trace_id = trace_id or os.getenv("TRACE_ID") or self.run_id
        self.parent_run_id = parent_run_id or os.getenv("PARENT_RUN_ID")
        self._sinks = self._build_sinks()

    def _build_sinks(self) -> list:
        sinks: list = [_StdoutSink()]
        supabase = _get_supabase_client()
        if supabase is not None:
            sinks.append(_SupabaseSink(supabase))
        sinks.append(_SentrySink())  # always-on; internally no-ops if sdk absent
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if chat_id and token:
            sinks.append(_MainChatSink(chat_id=chat_id))
        events_channel_id = os.getenv("TELEGRAM_EVENTS_CHANNEL_ID")
        if events_channel_id and token:
            client = _build_telegram_client()
            if client is not None:
                sinks.append(_EventsChannelSink(chat_id=events_channel_id, client=client))
        return sinks

    def emit(
        self,
        event: str,
        label: str = "",
        detail: Optional[dict] = None,
        level: str = "info",
    ) -> None:
        """Fan-out to all sinks. Never raises."""
        if level not in _VALID_LEVELS:
            level = "info"
        event_dict = {
            "ts": _now_iso(),
            "workflow": self.workflow,
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "parent_run_id": self.parent_run_id,
            "level": level,
            "event": event,
            "label": label or None,
            "detail": detail or None,
        }
        for sink in self._sinks:
            try:
                sink.emit(event_dict)
            except Exception as exc:
                # Never let sink failure propagate
                logger.warning("event_bus sink %s failed: %s", type(sink).__name__, exc)


class _StdoutSink:
    """Always-on sink: one JSON line per event to stdout. Surfaces in GH Actions logs."""

    def emit(self, event_dict: dict) -> None:
        sys.stdout.write(json.dumps(event_dict, ensure_ascii=False) + "\n")
        sys.stdout.flush()


class _SupabaseSink:
    """Persists each event to the event_log table. Best-effort."""

    def __init__(self, client):
        self._client = client

    def emit(self, event_dict: dict) -> None:
        # Strip the 'ts' from the row; let Supabase use its NOW() default.
        row = {k: v for k, v in event_dict.items() if k != "ts"}
        self._client.table("event_log").insert(row).execute()


class _SentrySink:
    """Adds a Sentry breadcrumb per event for crash context. No capture here —
    capture_exception lives in the @with_event_bus decorator."""

    def emit(self, event_dict: dict) -> None:
        try:
            import sentry_sdk
        except Exception:
            return  # sentry_sdk absent or shimmed to None
        if sentry_sdk is None:
            return
        sentry_sdk.add_breadcrumb(
            category=event_dict.get("workflow") or "event_bus",
            level=event_dict.get("level", "info"),
            message=event_dict.get("label") or event_dict.get("event", ""),
            data=event_dict.get("detail") or {},
        )


_ALERT_EVENTS = frozenset({"cron_crashed", "cron_missed"})


class _MainChatSink:
    """Sends a distinct Telegram message to the operator's main chat for errors
    and specific alert events. Skips info-level so the primary chat stays clean."""

    def __init__(self, chat_id: str):
        self._chat_id = chat_id

    def _should_alert(self, event_dict: dict) -> bool:
        if event_dict.get("level") in ("warn", "error"):
            return True
        if event_dict.get("event") in _ALERT_EVENTS:
            return True
        return False

    def emit(self, event_dict: dict) -> None:
        if not self._should_alert(event_dict):
            return
        client = _build_telegram_client()
        if client is None:
            return
        text = self._format(event_dict)
        client.send_message(text=text, chat_id=self._chat_id)

    @staticmethod
    def _format(event_dict: dict) -> str:
        workflow = (event_dict.get("workflow") or "?").upper().replace("_", " ")
        event = event_dict.get("event", "")
        label = event_dict.get("label") or ""
        run_id = event_dict.get("run_id", "")
        if event == "cron_crashed":
            emoji = "🚨"
            title = f"{workflow} — CRASH"
        elif event == "cron_missed":
            emoji = "⏰"
            title = f"{workflow} — NÃO RODOU"
        else:
            emoji = "⚠️"
            title = f"{workflow} — {event}"
        lines = [f"{emoji} {title}"]
        if label:
            lines.append(label)
        if run_id:
            lines.append(f"run_id: {run_id}")
        return "\n".join(lines)


class _EventsChannelSink:
    """Firehose sink: sends every emitted event to a dedicated Telegram channel.

    Unlike _MainChatSink (which only fires for warn/error/crashed/missed), this
    sink relays info events too — a complete audit trail the operator can silence
    and review on demand.

    Throttling:
    - warn/error events flush immediately (latency parity with _MainChatSink).
    - info events batch in 1-second windows OR at 20 events, whichever first.
    - End-of-run flush via atexit so a short-lived script doesn't lose its tail.

    Rate-limit posture: Telegram allows ~30 msg/sec/chat. With the 1s window we
    emit ≤1 msg/sec for info bursts, leaving headroom for warn/error spikes.
    """

    _BATCH_WINDOW_SECONDS = 1.0
    _MAX_BUFFER = 20  # flush when buffered info reaches this count

    def __init__(self, chat_id: str, client):
        self._chat_id = chat_id
        self._client = client
        self._buffer: list = []
        self._last_flush = _monotonic()
        # Register flush at interpreter exit so short scripts don't drop events
        atexit.register(self._flush_on_exit)

    def emit(self, event_dict: dict) -> None:
        level = event_dict.get("level", "info")
        if level in ("warn", "error"):
            # Preserve ordering: flush any buffered info first, then send this one
            self._flush()
            self._send_one([event_dict])
            return
        # Info: buffer then maybe flush
        self._buffer.append(event_dict)
        if len(self._buffer) >= self._MAX_BUFFER:
            self._flush()
            return
        now = _monotonic()
        if (now - self._last_flush) >= self._BATCH_WINDOW_SECONDS:
            self._flush()

    def _flush(self) -> None:
        if not self._buffer:
            return
        batch = self._buffer
        self._buffer = []
        self._last_flush = _monotonic()
        self._send_one(batch)

    def _flush_on_exit(self) -> None:
        try:
            self._flush()
        except Exception:
            pass  # atexit handlers must not raise

    def _send_one(self, events: list) -> None:
        text = self._format(events)
        try:
            self._client.send_message(text=text, chat_id=self._chat_id, parse_mode=None)
        except Exception as exc:
            logger.warning(f"_EventsChannelSink send failed: {exc}")

    @staticmethod
    def _format(events: list) -> str:
        lines = []
        level_emoji = {"info": "ℹ️", "warn": "⚠️", "error": "🚨"}
        for ev in events:
            ts = ev.get("ts") or ""
            hhmmss = ts[11:19] if len(ts) >= 19 else ts
            wf = ev.get("workflow") or "?"
            ev_name = ev.get("event") or "?"
            emoji = level_emoji.get(ev.get("level", "info"), "•")
            label = ev.get("label") or ""
            line = f"{hhmmss} {emoji} {wf}.{ev_name}"
            if label:
                line += f" — {label[:80]}"
            lines.append(line)
        return "\n".join(lines)


def with_event_bus(workflow: str):
    """Decorator that wraps a script's main() to emit lifecycle events and
    capture uncaught exceptions to Sentry.

    Usage:
        @with_event_bus("morning_check")
        def main():
            ...

    Emits cron_started on entry, cron_finished on clean exit, cron_crashed on
    exception. Calls init_sentry(workflow) as first action. Re-raises the
    original exception so GH Actions marks the run as failed.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Auto-init Sentry (idempotent; safe even if script already calls it)
            try:
                from execution.core.sentry_init import init_sentry
                init_sentry(f"cron.{workflow}")
            except Exception as exc:
                logger.warning("init_sentry failed in decorator: %s", exc)

            bus = EventBus(workflow=workflow)
            token = _active_bus.set(bus)
            try:
                bus.emit("cron_started")
                try:
                    result = func(*args, **kwargs)
                except BaseException as exc:
                    bus.emit(
                        "cron_crashed",
                        label=f"{type(exc).__name__}: {str(exc)[:100]}",
                        detail={"exc_type": type(exc).__name__, "exc_str": str(exc)[:500]},
                        level="error",
                    )
                    # Update state_store so the watchdog knows "tentou rodar e crashou"
                    # even if the script failed before progress_reporter.fail could fire
                    # (e.g., import-time exceptions, config-load failures).
                    # Deduped inside record_crash when progress.fail also runs.
                    try:
                        from execution.core import state_store
                        state_store.record_crash(workflow, f"{type(exc).__name__}: {exc}")
                    except Exception:
                        pass
                    # Capture WITH the last breadcrumbs already on the Sentry scope
                    try:
                        import sentry_sdk
                        if sentry_sdk is not None:
                            sentry_sdk.capture_exception(exc)
                    except Exception:
                        pass
                    raise
                bus.emit("cron_finished")
                return result
            finally:
                _active_bus.reset(token)
        return wrapper
    return decorator
