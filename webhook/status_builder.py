"""
Status message builder for the /status workflow health command.

Provides ALL_WORKFLOWS, _format_status_lines, and build_status_message.
Heavy execution imports (state_store, cron_parser) are kept as lazy imports
inside function bodies so this module loads without the full execution stack.
"""

import logging
import os
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

ALL_WORKFLOWS = [
    "morning_check",
    "daily_report",
    "baltic_ingestion",
    "market_news",
    "rationale_news",
    "onedrive_resubscribe",
]


def _format_status_lines(states: dict, next_runs: dict) -> list:
    """Build per-workflow lines for the /status response."""
    max_name = max(len(w) for w in states.keys()) if states else 0
    lines = []
    for workflow, st in states.items():
        # Escape underscores so Telegram Markdown doesn't interpret them as italic markers
        label = (workflow.replace("_", r"\_") + ":").ljust(max_name + 4)
        if st is not None and st.get("streak", 0) >= 3:
            lines.append(f"{label} 🚨 {st['streak']} falhas seguidas")
            continue
        if st is None:
            nxt = next_runs.get(workflow)
            when = nxt.strftime("%H:%M") if nxt else "?"
            lines.append(f"{label} ⏳ proximo {when} BRT")
            continue
        status = st.get("status")
        time_iso = st.get("time_iso", "")
        try:
            hhmm = time_iso[11:16]
        except Exception:
            hhmm = "??:??"
        if status == "success":
            summary = st.get("summary", {})
            ok = summary.get("success", 0)
            total = summary.get("total", 0)
            dur_ms = st.get("duration_ms", 0)
            dur = f"{dur_ms // 60000}m" if dur_ms >= 60000 else f"{dur_ms // 1000}s"
            lines.append(f"{label} ✅ {hhmm} BRT ({ok}/{total}, {dur})")
        elif status == "failure":
            summary = st.get("summary", {})
            total = summary.get("total", 0)
            lines.append(f"{label} ❌ {hhmm} BRT (0/{total} enviadas)")
        elif status == "crash":
            reason = (st.get("reason") or "")[:40]
            lines.append(f"{label} 💥 {hhmm} BRT (crash: {reason})")
        elif status == "empty":
            reason = st.get("reason", "")
            lines.append(f"{label} ℹ️ {hhmm} BRT ({reason})")
        else:
            lines.append(f"{label} ? estado desconhecido")
    return lines


def build_status_message() -> str:
    """Fetch state + cron + format full /status body."""
    from execution.core import state_store, cron_parser
    brt = timezone(timedelta(hours=-3))
    states = state_store.get_all_status(ALL_WORKFLOWS)
    if all(v is None for v in states.values()):
        # Probe if Redis itself is dead (not just "never recorded")
        if state_store._get_client() is None:
            return "⚠️ Store de estado indisponivel. Abra o dashboard pra ver historico."
    next_runs = {wf: cron_parser.parse_next_run(wf) for wf in ALL_WORKFLOWS}
    header = datetime.now(brt).strftime("📊 STATUS (%d/%m %H:%M BRT)")
    lines = _format_status_lines(states, next_runs)
    dashboard_url = os.getenv("DASHBOARD_BASE_URL", "https://workflows-minerals.vercel.app")
    return header + "\n\n" + "\n".join(lines) + f"\n\n[Dashboard]({dashboard_url}/)"
