"""Sentry initialization helper for execution scripts.

Usage:
    from execution.core.sentry_init import init_sentry
    init_sentry(__name__)  # at the top of the script's main()
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def init_sentry(script_name: str) -> bool:
    """Initialize Sentry for a cron/execution script.

    Returns True if Sentry was initialized, False if disabled (no DSN).
    """
    dsn = os.getenv("SENTRY_DSN", "")
    if not dsn:
        logger.warning("SENTRY_DSN not set — Sentry disabled for %s", script_name)
        return False
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=dsn,
            environment=os.getenv("RAILWAY_ENVIRONMENT", "dev"),
            traces_sample_rate=0.1,
        )
        sentry_sdk.set_tag("script", script_name)
        logger.info("sentry_initialized for script=%s", script_name)
        return True
    except Exception as exc:
        logger.warning("sentry_init_failed: %s", exc)
        return False
