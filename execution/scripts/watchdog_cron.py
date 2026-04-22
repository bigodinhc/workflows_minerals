"""
Watchdog: detects when a workflow in ALL_WORKFLOWS was supposed to run but didn't.

Runs every 5 minutes via .github/workflows/watchdog.yml. For each workflow:
  1. Computes `previous_expected` = most recent past cron occurrence.
  2. If `now < previous_expected + GRACE_MINUTES`, still in grace window — skip.
  3. Reads `state_store.get_status(wf).time_iso`. If >= previous_expected, ran — skip.
  4. Otherwise: atomically claim an alert key (idempotency), then emit `cron_missed`.

The event bus (Phase 1) fans out to stdout (GH Actions logs), Supabase event_log,
Sentry breadcrumb, and the main-chat Telegram sink — which delivers the actual
operator alert.

Never raises at the top level — wrapped by @with_event_bus so the GH run marks
failed if something unrecoverable happens.
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from execution.core import cron_parser, state_store
from execution.core.event_bus import EventBus, with_event_bus
from webhook import status_builder

logger = logging.getLogger(__name__)

GRACE_MINUTES = 15
ALERT_TTL_SECONDS = 86_400  # 24h; one alert per miss-occurrence


def _utc_now() -> datetime:
    """Monkeypatch seam for tests."""
    return datetime.now(timezone.utc)


def _parse_iso_to_utc(iso_str: str) -> Optional[datetime]:
    """Parse an ISO-8601 string to a UTC-aware datetime, or None if unparseable."""
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@with_event_bus("watchdog")
def main() -> None:
    bus = EventBus(workflow="watchdog")
    now = _utc_now()

    for workflow in status_builder.ALL_WORKFLOWS:
        previous_expected = cron_parser.parse_previous_run(workflow)
        if previous_expected is None:
            continue  # workflow has no schedule YAML or unparseable — skip

        deadline = previous_expected + timedelta(minutes=GRACE_MINUTES)
        if now < deadline:
            continue  # still in grace window

        last = state_store.get_status(workflow)
        last_run_utc = _parse_iso_to_utc(last.get("time_iso") if last else "")
        if last_run_utc is not None and last_run_utc >= previous_expected:
            continue  # ran (possibly late, but ran)

        alert_key = f"wf:watchdog_alerted:{workflow}:{previous_expected.isoformat()}"
        if not state_store.try_claim_alert_key(alert_key, ALERT_TTL_SECONDS):
            continue  # already alerted for this miss

        bus.emit(
            "cron_missed",
            label=f"{workflow} não rodou",
            detail={
                "missed_workflow": workflow,
                "expected_iso": previous_expected.isoformat(),
                "deadline_iso": deadline.isoformat(),
                "last_run_iso": last.get("time_iso") if last else None,
                "grace_minutes": GRACE_MINUTES,
            },
            level="error",
        )


if __name__ == "__main__":
    main()
