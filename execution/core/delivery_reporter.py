"""
Delivery reporter: shared module for tracking WhatsApp send results
across GH Actions scripts and webhook flows.

Emits structured JSON to stdout (for dashboard parsing) and sends
Telegram summary notification at end of dispatch.
"""
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Iterable, Optional


class SendErrorCategory(Enum):
    """Categories of send failures. Used for alert grouping, action hints,
    circuit breaker decisions, and Sentry tagging."""
    WHATSAPP_DISCONNECTED = "whatsapp_disconnected"
    RATE_LIMIT = "rate_limit"
    INVALID_NUMBER = "invalid_number"
    UPSTREAM_5XX = "upstream_5xx"
    AUTH = "auth"
    TIMEOUT = "timeout"
    NETWORK = "network"
    UNKNOWN = "unknown"
    SKIPPED_CIRCUIT_BREAK = "skipped_circuit_break"


# Human-readable PT labels per category (shown in the grouped summary)
_CATEGORY_LABEL = {
    SendErrorCategory.WHATSAPP_DISCONNECTED: "WhatsApp desconectado",
    SendErrorCategory.RATE_LIMIT: "Rate limit",
    SendErrorCategory.INVALID_NUMBER: "Número inválido",
    SendErrorCategory.UPSTREAM_5XX: "Erro UazAPI (5xx)",
    SendErrorCategory.AUTH: "Falha de autenticação",
    SendErrorCategory.TIMEOUT: "Timeout",
    SendErrorCategory.NETWORK: "Erro de rede",
    SendErrorCategory.UNKNOWN: "Erro não categorizado",
    SendErrorCategory.SKIPPED_CIRCUIT_BREAK: "Pulados pelo circuit breaker",
}

# Action hint per category. None means no hint (transient, no operator action).
_CATEGORY_HINT = {
    SendErrorCategory.WHATSAPP_DISCONNECTED: "Reconecte QR em mineralstrading.uazapi.com",
    SendErrorCategory.AUTH: "Verifique UAZAPI_TOKEN no secrets do GitHub",
    SendErrorCategory.INVALID_NUMBER: "Revise a planilha de contatos",
    SendErrorCategory.UPSTREAM_5XX: "Verifique status do UazAPI",
    SendErrorCategory.RATE_LIMIT: None,
    SendErrorCategory.TIMEOUT: None,
    SendErrorCategory.NETWORK: None,
    SendErrorCategory.UNKNOWN: "Veja logs do GitHub Actions",
    SendErrorCategory.SKIPPED_CIRCUIT_BREAK: None,
}

# Categories considered "fatal" — N consecutive failures in the same one triggers abort.
# Transient categories (timeout, network) do NOT trip the breaker.
_FATAL_CATEGORIES = frozenset({
    SendErrorCategory.WHATSAPP_DISCONNECTED,
    SendErrorCategory.AUTH,
    SendErrorCategory.UPSTREAM_5XX,
})

_CIRCUIT_BREAKER_THRESHOLD = 5

# Per-category how many sample contact names to show inline (0 = none, show count only)
_CATEGORY_SAMPLE_LIMIT = 3


@dataclass
class Contact:
    """A WhatsApp contact (name + phone)."""
    name: str
    phone: str


@dataclass
class DeliveryResult:
    """Result of a single delivery attempt."""
    contact: Contact
    success: bool
    error: Optional[str]
    duration_ms: int
    category: SendErrorCategory = SendErrorCategory.UNKNOWN


@dataclass
class DeliveryReport:
    """Aggregated report of all deliveries in a dispatch."""
    workflow: str
    started_at: datetime
    finished_at: datetime
    results: list

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def success_count(self) -> int:
        return sum(1 for r in self.results if r.success)

    @property
    def failure_count(self) -> int:
        return sum(1 for r in self.results if not r.success)

    @property
    def failures(self) -> list:
        return [r for r in self.results if not r.success]


def _extract_http_reason(exc: Exception) -> str:
    """Extract a human-readable reason from a requests.HTTPError's JSON body.

    Handles UazAPI-style bodies: {"error": bool, "message": str} or {"error": str}.
    Returns the reason truncated to 120 chars, or empty string if the body is
    not JSON, is not a dict, or has no usable message/error field. Callers decide
    their own fallback (category decision tree vs. legacy error string).
    Returns "" if exc has no `.response` attribute (defensive).
    """
    import json as _json
    response = getattr(exc, "response", None)
    body = (getattr(response, "text", None) or "") if response is not None else ""
    try:
        parsed = _json.loads(body)
    except (ValueError, TypeError):
        return ""
    if not isinstance(parsed, dict):
        return ""
    raw_error = parsed.get("error")
    candidate = parsed.get("message") if isinstance(raw_error, bool) else raw_error
    reason = str(candidate or parsed.get("message") or "")[:120]
    return reason


def classify_error(exc: Exception) -> tuple[SendErrorCategory, str]:
    """Classify an exception raised by a WhatsApp send into (category, reason).

    The reason is a short, human-readable string suitable for the Telegram alert.
    The category drives action hints, grouping, and circuit breaker behavior.
    """
    import requests as _rq

    if isinstance(exc, _rq.Timeout):
        return SendErrorCategory.TIMEOUT, "timeout"

    if isinstance(exc, _rq.ConnectionError):
        return SendErrorCategory.NETWORK, str(exc)[:120]

    if isinstance(exc, _rq.HTTPError) and exc.response is not None:
        status = exc.response.status_code
        # Helper caps structured reasons at 120 chars; raw-body fallback uses
        # 100 chars to stay consistent with _categorize_error's legacy fallback.
        reason_str = _extract_http_reason(exc) or (exc.response.text or "")[:100]
        reason_lower = reason_str.lower()

        # Category decision tree
        if status == 401 or status == 403:
            return SendErrorCategory.AUTH, reason_str or f"HTTP {status}"
        if status == 429 or ("rate" in reason_lower and "limit" in reason_lower):
            return SendErrorCategory.RATE_LIMIT, reason_str or f"HTTP {status}"
        if "disconnected" in reason_lower or "not connected" in reason_lower:
            return SendErrorCategory.WHATSAPP_DISCONNECTED, reason_str
        if status == 400 and ("not registered" in reason_lower or "invalid number" in reason_lower or "not on whatsapp" in reason_lower):
            return SendErrorCategory.INVALID_NUMBER, reason_str
        if 500 <= status < 600:
            return SendErrorCategory.UPSTREAM_5XX, reason_str or f"HTTP {status}"

        return SendErrorCategory.UNKNOWN, reason_str or f"HTTP {status}"

    return SendErrorCategory.UNKNOWN, str(exc)[:200]


def _categorize_error(exc: Exception, reason: Optional[str] = None) -> str:
    """Convert exception into short error category string (legacy format for
    dashboard JSON compat): "timeout", "HTTP N: <reason>", or str(exc)[:200].

    When `reason` is provided (pre-extracted by classify_error), skips the
    JSON re-parse. When not provided, extracts independently via
    `_extract_http_reason`. Falls back to truncated raw body when no structured
    reason is available, preserving historical dashboard behavior.
    """
    import requests as _rq
    if isinstance(exc, _rq.Timeout):
        return "timeout"
    if isinstance(exc, _rq.HTTPError) and exc.response is not None:
        status = exc.response.status_code
        if reason is None:
            reason = _extract_http_reason(exc)
        if reason:
            return f"HTTP {status}: {reason}"
        body = exc.response.text or ""
        return f"HTTP {status}: {body[:100]}"
    return str(exc)[:200]


def _group_failures_by_category(failures: list) -> list:
    """Return list of (category, results) tuples sorted by count descending.

    Skipped-by-circuit-breaker entries are excluded — they render as a
    separate trailing footnote, not as a competing bucket.
    """
    from collections import defaultdict
    buckets: dict = defaultdict(list)
    for f in failures:
        if f.category == SendErrorCategory.SKIPPED_CIRCUIT_BREAK:
            continue
        buckets[f.category].append(f)
    return sorted(buckets.items(), key=lambda kv: -len(kv[1]))


def _format_telegram_message(
    report: DeliveryReport,
    dashboard_base_url: str,
    gh_run_id: Optional[str],
) -> str:
    """Build Telegram-ready text summary of a DeliveryReport.

    Failures are grouped by SendErrorCategory. Each group shows count,
    PT-BR label, optional action hint, and up to 3 sample contact names.
    """
    failure_pct = (report.failure_count / report.total * 100) if report.total else 0

    if report.failure_count == 0:
        emoji = "✅"
        header = f"{emoji} {report.workflow.upper().replace('_', ' ')}"
    elif failure_pct > 50:
        emoji = "🚨"
        header = f"{emoji} {report.workflow.upper().replace('_', ' ')} — FALHA TOTAL"
    else:
        emoji = "⚠️"
        header = f"{emoji} {report.workflow.upper().replace('_', ' ')}"

    duration = report.finished_at - report.started_at
    minutes = int(duration.total_seconds() // 60)
    seconds = int(duration.total_seconds() % 60)
    dur_str = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
    when = report.started_at.strftime("%d/%m/%Y %H:%M")

    lines = [header, f"{when} ({dur_str})", ""]
    lines.append(
        f"📊 Total: {report.total} | OK: {report.success_count} | "
        f"Falha: {report.failure_count}"
    )
    lines.append("")

    if report.failure_count == 0:
        lines.append("Todos os contatos receberam.")
    else:
        lines.append("❌ FALHAS POR TIPO:")
        for category, failures in _group_failures_by_category(report.failures):
            label = _CATEGORY_LABEL.get(category, category.value)
            hint = _CATEGORY_HINT.get(category)
            count = len(failures)
            lines.append(f"• {count}× {label}")
            if count <= _CATEGORY_SAMPLE_LIMIT:
                names = ", ".join(f.contact.name for f in failures)
                lines.append(f"  ({names})")
            if hint:
                lines.append(f"  → AÇÃO: {hint}")

        # Trailing footnote: circuit breaker skipped contacts, shown after the
        # real failure categories so the actionable cause stays at the top.
        skipped_count = sum(
            1 for f in report.failures
            if f.category == SendErrorCategory.SKIPPED_CIRCUIT_BREAK
        )
        if skipped_count > 0:
            lines.append("")
            lines.append(f"ℹ️ {skipped_count} contatos pulados pelo circuit breaker")

    link = (
        f"{dashboard_base_url}/?run_id={gh_run_id}"
        if gh_run_id
        else f"{dashboard_base_url}/"
    )
    lines.append("")
    lines.append(f"[Ver no dashboard]({link})")

    return "\n".join(lines)


def _build_telegram_client():
    """Factory for TelegramClient. Separate function to allow test monkeypatching."""
    from execution.integrations.telegram_client import TelegramClient
    return TelegramClient()


def build_contact_from_row(row: dict) -> Optional[Contact]:
    """
    Convert a Google Sheets row dict into a Contact.
    Returns None if no phone field is present/usable.
    Priority for name: ProfileName > Nome > Name > "—".
    Priority for phone: Evolution-api > n8n-evo > Telefone > Phone > From.
    Phone normalization: strip "whatsapp:", "+", "@s.whatsapp.net".
    """
    name = (
        row.get("ProfileName")
        or row.get("Nome")
        or row.get("Name")
        or "—"
    )
    raw_phone = (
        row.get("Evolution-api")
        or row.get("n8n-evo")
        or row.get("Telefone")
        or row.get("Phone")
        or row.get("From")
    )
    if not raw_phone:
        return None
    phone = (
        str(raw_phone)
        .replace("whatsapp:", "")
        .replace("@s.whatsapp.net", "")
        .replace("+", "")
        .strip()
    )
    if not phone:
        return None
    return Contact(name=name, phone=phone)


class DeliveryReporter:
    """Shared delivery tracker for WhatsApp workflows."""

    def __init__(
        self,
        workflow: str,
        send_fn: Callable[[str, str], None],
        notify_telegram: bool = True,
        telegram_chat_id: Optional[str] = None,
        dashboard_base_url: str = "https://workflows-minerals.vercel.app",
        gh_run_id: Optional[str] = None,
        circuit_breaker_threshold: int = _CIRCUIT_BREAKER_THRESHOLD,
        fatal_categories: frozenset[SendErrorCategory] = _FATAL_CATEGORIES,
    ):
        self.workflow = workflow
        self.send_fn = send_fn
        self.notify_telegram = notify_telegram
        self.telegram_chat_id = telegram_chat_id
        self.dashboard_base_url = dashboard_base_url
        self.gh_run_id = gh_run_id
        self.circuit_breaker_threshold = circuit_breaker_threshold
        self.fatal_categories = fatal_categories

    def dispatch(
        self,
        contacts: Iterable[Contact],
        message: str,
        on_progress: Optional[Callable[[int, int, DeliveryResult], None]] = None,
    ) -> DeliveryReport:
        """Send `message` to each contact. Never raises on send failure.

        Circuit breaker: when `circuit_breaker_threshold` consecutive failures
        all share the same category AND that category is in `fatal_categories`,
        the remaining contacts are skipped and marked with error
        'skipped_due_to_circuit_break'.
        """
        import time
        started_at = datetime.now().astimezone()
        results: list = []
        contacts_list = list(contacts)
        total = len(contacts_list)

        streak_category: Optional[SendErrorCategory] = None
        streak_count = 0
        circuit_tripped = False

        for i, contact in enumerate(contacts_list):
            if circuit_tripped:
                result = DeliveryResult(
                    contact=contact,
                    success=False,
                    error="skipped_due_to_circuit_break",
                    duration_ms=0,
                    category=SendErrorCategory.SKIPPED_CIRCUIT_BREAK,
                )
                results.append(result)
                if on_progress is not None:
                    try:
                        on_progress(i + 1, total, result)
                    except Exception:
                        pass
                continue

            t0 = time.monotonic()
            success = False
            error: Optional[str] = None
            category: SendErrorCategory = SendErrorCategory.UNKNOWN
            try:
                self.send_fn(contact.phone, message)
                success = True
            except Exception as exc:
                category, reason = classify_error(exc)
                error = _categorize_error(exc, reason)  # single JSON parse; legacy string for dashboard JSON
                self._capture_sentry(exc, category)
            duration_ms = int((time.monotonic() - t0) * 1000)

            result = DeliveryResult(
                contact=contact,
                success=success,
                error=error,
                duration_ms=duration_ms,
                category=category,
            )
            results.append(result)

            # Circuit breaker bookkeeping
            if success:
                streak_category = None
                streak_count = 0
            else:
                if category == streak_category:
                    streak_count += 1
                else:
                    streak_category = category
                    streak_count = 1
                if (
                    streak_count >= self.circuit_breaker_threshold
                    and streak_category in self.fatal_categories
                ):
                    circuit_tripped = True

            if on_progress is not None:
                try:
                    on_progress(i + 1, total, result)
                except Exception:
                    pass  # progress callback failures do not abort dispatch

        finished_at = datetime.now().astimezone()
        report = DeliveryReport(
            workflow=self.workflow,
            started_at=started_at,
            finished_at=finished_at,
            results=results,
        )
        self._emit_stdout_report(report)
        self._emit_delivery_summary_event(report)
        if self.notify_telegram:
            self._send_telegram_summary(report)
        return report

    @staticmethod
    def _emit_delivery_summary_event(report: "DeliveryReport") -> None:
        """Emit a single `delivery_summary` event to the active EventBus so the
        events channel card shows the dispatch outcome (not just 'Enviando...'
        frozen in ⏳). No-op when no bus is active (tests, isolated scripts).

        Intentionally does NOT emit per-contact ticks — that would exceed the
        Telegram edit-rate per message and leak contact PII to the firehose."""
        try:
            from execution.core.event_bus import get_current_bus
        except Exception:
            return
        bus = get_current_bus()
        if bus is None:
            return
        level = "warn" if report.failure_count > 0 else "info"
        label = f"{report.success_count}/{report.total} enviadas"
        if report.failure_count > 0:
            label += f", {report.failure_count} falha{'s' if report.failure_count > 1 else ''}"
        try:
            bus.emit(
                "delivery_summary",
                label=label,
                detail={
                    "total": report.total,
                    "success": report.success_count,
                    "failure": report.failure_count,
                },
                level=level,
            )
        except Exception:
            pass  # bus.emit already swallows sink failures; belt-and-suspenders

    def _emit_stdout_report(self, report: DeliveryReport) -> None:
        """Print structured JSON report delimited by markers for dashboard parsing."""
        import json as _json
        payload = {
            "workflow": report.workflow,
            "started_at": report.started_at.isoformat(),
            "finished_at": report.finished_at.isoformat(),
            "duration_seconds": int((report.finished_at - report.started_at).total_seconds()),
            "summary": {
                "total": report.total,
                "success": report.success_count,
                "failure": report.failure_count,
            },
            "results": [
                {
                    "name": r.contact.name,
                    "phone": r.contact.phone,
                    "success": r.success,
                    "error": r.error,
                    "duration_ms": r.duration_ms,
                }
                for r in report.results
            ],
        }
        print("<<<DELIVERY_REPORT_START>>>")
        print(_json.dumps(payload, indent=2, ensure_ascii=False))
        print("<<<DELIVERY_REPORT_END>>>")

    def _send_telegram_summary(self, report: DeliveryReport) -> None:
        """Send final delivery summary to Telegram. Never raises."""
        try:
            text = _format_telegram_message(
                report,
                dashboard_base_url=self.dashboard_base_url,
                gh_run_id=self.gh_run_id,
            )
            client = _build_telegram_client()
            client.send_message(
                text=text,
                chat_id=self.telegram_chat_id,
                parse_mode="Markdown",
            )
        except Exception as exc:
            print(f"[WARN] Failed to send Telegram summary: {exc}")

    def _capture_sentry(self, exc: Exception, category: SendErrorCategory) -> None:
        """Capture exception to Sentry with category as a searchable tag.
        Silent no-op if sentry_sdk is not importable or not initialized.
        """
        try:
            import sentry_sdk
            with sentry_sdk.new_scope() as scope:
                scope.set_tag("send.category", category.value)
                scope.set_tag("workflow", self.workflow)
                sentry_sdk.capture_exception(exc)
        except Exception:
            pass  # never let telemetry failures break dispatch
