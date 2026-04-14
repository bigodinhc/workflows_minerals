"""
Delivery reporter: shared module for tracking WhatsApp send results
across GH Actions scripts and webhook flows.

Emits structured JSON to stdout (for dashboard parsing) and sends
Telegram summary notification at end of dispatch.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


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


import time
from typing import Callable, Iterable


def _categorize_error(exc: Exception) -> str:
    """Convert exception into short error category string.
    For HTTP errors, tries to extract 'error' or 'message' field from JSON
    bodies (UazAPI style) before falling back to truncated raw body.
    """
    import requests as _rq
    import json as _json
    if isinstance(exc, _rq.Timeout):
        return "timeout"
    if isinstance(exc, _rq.HTTPError) and exc.response is not None:
        status = exc.response.status_code
        body = exc.response.text or ""
        try:
            parsed = _json.loads(body)
            if isinstance(parsed, dict):
                reason = parsed.get("error") or parsed.get("message")
                if reason:
                    return f"HTTP {status}: {str(reason)[:120]}"
        except (ValueError, TypeError):
            pass
        return f"HTTP {status}: {body[:100]}"
    return str(exc)[:200]


_MAX_FAILURES_LISTED = 15


def _format_telegram_message(
    report: DeliveryReport,
    dashboard_base_url: str,
    gh_run_id: Optional[str],
) -> str:
    """Build Telegram-ready text summary of a DeliveryReport."""
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
    elif failure_pct > 50 and report.success_count == 0 and report.failure_count <= _MAX_FAILURES_LISTED:
        lines.append("Todos os envios falharam. Verifique:")
        lines.append("• Token UAZAPI")
        lines.append("• Status do servico UazAPI")
        lines.append("• Logs do GitHub Actions")
        first_err = report.failures[0].error if report.failures else "unknown"
        lines.append("")
        lines.append(f"Primeira falha: {first_err}")
    else:
        lines.append("❌ FALHAS:")
        listed = report.failures[:_MAX_FAILURES_LISTED]
        for f in listed:
            lines.append(f"• {f.contact.name} ({f.contact.phone}) — {f.error}")
        remaining = len(report.failures) - len(listed)
        if remaining > 0:
            lines.append(f"...e mais {remaining} falhas")

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
    ):
        self.workflow = workflow
        self.send_fn = send_fn
        self.notify_telegram = notify_telegram
        self.telegram_chat_id = telegram_chat_id
        self.dashboard_base_url = dashboard_base_url
        self.gh_run_id = gh_run_id

    def dispatch(
        self,
        contacts: Iterable[Contact],
        message: str,
        on_progress: Optional[Callable[[int, int, DeliveryResult], None]] = None,
    ) -> DeliveryReport:
        """Send `message` to each contact. Never raises on send failure."""
        started_at = datetime.now().astimezone()
        results: list = []
        contacts_list = list(contacts)
        total = len(contacts_list)

        for i, contact in enumerate(contacts_list):
            t0 = time.monotonic()
            success = False
            error: Optional[str] = None
            try:
                self.send_fn(contact.phone, message)
                success = True
            except Exception as exc:
                error = _categorize_error(exc)
            duration_ms = int((time.monotonic() - t0) * 1000)

            result = DeliveryResult(
                contact=contact,
                success=success,
                error=error,
                duration_ms=duration_ms,
            )
            results.append(result)

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
        if self.notify_telegram:
            self._send_telegram_summary(report)
        return report

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
