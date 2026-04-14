"""Deterministic ID generation for Platts items.

sha256(source + "::" + title) truncated to 12 hex chars. Stable cross-run,
enabling dedup via Redis SET and matching between staging/archive keys.
"""
import hashlib


def generate_id(source: str, title: str) -> str:
    """Generate a 12-char hex ID from source + title."""
    digest = hashlib.sha256(f"{source}::{title}".encode("utf-8")).hexdigest()
    return digest[:12]
