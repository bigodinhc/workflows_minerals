"""
Event bus: single-point emitter for structured workflow events.

Fan-outs to multiple sinks (stdout, Supabase event_log, Sentry breadcrumbs,
main-chat Telegram for errors). Every sink is never-raise — failures are
logged to stderr/logger and swallowed so workflows are never broken by
telemetry.

Phase 1 (this module): stdout + Supabase + Sentry + main-chat sinks.
Phase 2 (later): _EventsChannelSink for firehose.
"""
import json
import logging
import os
import secrets
import sys
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_VALID_LEVELS = frozenset({"info", "warn", "error"})


def _generate_run_id() -> str:
    """8-char hex, good enough for log grepping and far-from-collision."""
    return secrets.token_hex(4)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
