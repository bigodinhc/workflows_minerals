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
