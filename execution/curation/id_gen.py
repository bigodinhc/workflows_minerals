"""Deterministic ID generation for Platts items.

sha256(normalize(title)) truncated to 12 hex chars. Stable cross-run,
enabling dedup via Redis Sorted Set and matching between staging/archive keys.

v2: dropped source from hash — same article appears in multiple Platts pages
(e.g., "Latest" vs "Top News - Ferrous Metals") and must produce one canonical ID.
"""
import hashlib
import re

_CURLY_QUOTES = str.maketrans("\u2018\u2019\u201c\u201d", "''\"\"")
_TRAILING_PUNCT_RE = re.compile(r"[.,;]+$")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    """Normalize a title for canonical ID generation.

    Raises ValueError if the result is empty.
    """
    if not title or not isinstance(title, str):
        raise ValueError("title must be a non-empty string")
    result = title.strip().lower()
    result = _WHITESPACE_RE.sub(" ", result)
    result = result.translate(_CURLY_QUOTES)
    result = _TRAILING_PUNCT_RE.sub("", result)
    if not result:
        raise ValueError("title must be a non-empty string")
    return result


def generate_id(title: str) -> str:
    """Generate a 12-char hex ID from normalized title."""
    normalized = normalize_title(title)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
