"""Prometheus counters and histograms for the webhook service.

Import the names from this module directly:
    from metrics import whatsapp_sent, edit_failures
    whatsapp_sent.labels(status="success").inc()
"""
from __future__ import annotations

from prometheus_client import Counter, Histogram

# ── WhatsApp delivery ──
whatsapp_sent = Counter(
    "whatsapp_messages_total",
    "WhatsApp send outcomes",
    ["status"],  # success | failure | duplicate
)
whatsapp_duration = Histogram(
    "whatsapp_duration_seconds",
    "WhatsApp send latency (seconds)",
)

# ── Telegram edit errors ──
edit_failures = Counter(
    "telegram_edit_failures_total",
    "edit_message_text failures by reason",
    ["reason"],  # not_modified | bad_request | unexpected | flood
)

# ── ProgressReporter card edits ──
progress_card_edits = Counter(
    "progress_card_edits_total",
    "ProgressReporter Telegram card edits",
)
