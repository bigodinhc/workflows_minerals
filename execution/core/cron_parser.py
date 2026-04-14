"""
Parse GitHub Actions workflow YAML files to compute the next scheduled run
in BRT (America/Sao_Paulo). Used by the webhook /status command.

Never raises: returns None on any parse/IO/schedule failure.
"""
import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

_BRT = timezone(timedelta(hours=-3))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_next_run(workflow: str, workflows_dir: str = ".github/workflows") -> Optional[datetime]:
    """Return the next scheduled run of `workflow` in BRT, or None.
    `workflow` is the base filename (without .yml) in `workflows_dir`."""
    path = os.path.join(workflows_dir, f"{workflow}.yml")
    if not os.path.exists(path):
        return None
    try:
        import yaml
    except Exception as exc:
        logger.warning(f"cron_parser: pyyaml not installed: {exc}")
        return None
    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f)
    except Exception as exc:
        logger.warning(f"cron_parser: failed to parse {path}: {exc}")
        return None
    if not isinstance(data, dict):
        return None
    # GH Actions accepts `on:` as dict or as string; schedule only makes sense in dict form.
    # PyYAML parses `on:` as boolean True in some configurations; handle both.
    on_section = data.get("on") or data.get(True)
    if not isinstance(on_section, dict):
        return None
    schedule = on_section.get("schedule")
    if not isinstance(schedule, list) or not schedule:
        return None
    try:
        from croniter import croniter
    except Exception as exc:
        logger.warning(f"cron_parser: croniter not installed: {exc}")
        return None
    now_utc = _utc_now()
    next_runs = []
    for entry in schedule:
        cron_expr = entry.get("cron") if isinstance(entry, dict) else None
        if not cron_expr:
            continue
        try:
            it = croniter(cron_expr, now_utc)
            next_runs.append(it.get_next(datetime))
        except Exception as exc:
            logger.warning(f"cron_parser: bad cron {cron_expr!r}: {exc}")
            continue
    if not next_runs:
        return None
    earliest = min(next_runs)
    # croniter returns naive datetime in the given base's tz; we passed UTC-aware
    if earliest.tzinfo is None:
        earliest = earliest.replace(tzinfo=timezone.utc)
    return earliest.astimezone(_BRT)
