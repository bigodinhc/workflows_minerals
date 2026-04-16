# Scrap Dedup Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate duplicate items in staging/archive by switching to title-only canonical IDs and a global (non-dated) dedup set.

**Architecture:** Change `generate_id` to hash normalized title only (dropping source), replace per-day `platts:seen:<date>` SETs with a single `platts:seen` Sorted Set (30d rolling window), add staging-exists short-circuit in the router, and add `platts:scraped:<date>` for `/stats` telemetry. One-shot migration script rebuilds the global seen-set from existing archives.

**Tech Stack:** Python 3.11, redis-py, fakeredis, pytest

---

## File structure

| File | Role |
|------|------|
| `execution/curation/id_gen.py` | `normalize_title` + new `generate_id(title)` |
| `execution/curation/redis_client.py` | Global seen-set, `staging_exists`, `mark_scraped` |
| `execution/curation/router.py` | Updated dedup flow with staging short-circuit |
| `webhook/redis_queries.py` | `stats_for_date` reads `platts:scraped:<date>` |
| `execution/scripts/rebuild_dedup.py` | One-shot migration script |
| `tests/test_curation_id_gen.py` | Tests for normalize + new generate_id |
| `tests/test_curation_redis_client.py` | Tests for global seen, staging_exists, mark_scraped |
| `tests/test_curation_router.py` | H1/H2 regression tests, staging short-circuit |
| `tests/test_redis_queries.py` | Updated stats test for `platts:scraped` |
| `tests/test_rebuild_dedup.py` | Migration script tests |

---

### Task 1: normalize_title + new generate_id signature

**Files:**
- Modify: `execution/curation/id_gen.py`
- Modify: `tests/test_curation_id_gen.py`

- [ ] **Step 1: Replace existing tests with new test file**

Replace the full contents of `tests/test_curation_id_gen.py` with:

```python
"""Tests for execution.curation.id_gen (v2: title-only canonical ID)."""
import pytest


def test_normalize_title_strips_whitespace():
    from execution.curation.id_gen import normalize_title
    assert normalize_title("  hello  ") == "hello"


def test_normalize_title_lowercases():
    from execution.curation.id_gen import normalize_title
    assert normalize_title("EU Steel Deal") == "eu steel deal"


def test_normalize_title_collapses_internal_whitespace():
    from execution.curation.id_gen import normalize_title
    assert normalize_title("china  steel   output") == "china steel output"


def test_normalize_title_normalizes_curly_quotes():
    from execution.curation.id_gen import normalize_title
    assert normalize_title("\u2018hello\u2019 \u201cworld\u201d") == "'hello' \"world\""


def test_normalize_title_strips_trailing_punctuation():
    from execution.curation.id_gen import normalize_title
    assert normalize_title("Title.") == "title"
    assert normalize_title("Title,") == "title"
    assert normalize_title("Title;") == "title"


def test_normalize_title_preserves_internal_punctuation():
    from execution.curation.id_gen import normalize_title
    assert normalize_title("U.S. steel tariffs") == "u.s. steel tariffs"


def test_normalize_title_raises_on_empty():
    from execution.curation.id_gen import normalize_title
    with pytest.raises(ValueError):
        normalize_title("")


def test_normalize_title_raises_on_whitespace_only():
    from execution.curation.id_gen import normalize_title
    with pytest.raises(ValueError):
        normalize_title("   ")


def test_normalize_title_raises_on_none():
    from execution.curation.id_gen import normalize_title
    with pytest.raises(ValueError):
        normalize_title(None)


def test_generate_id_deterministic():
    from execution.curation.id_gen import generate_id
    a = generate_id("China steel output lags 2025")
    b = generate_id("China steel output lags 2025")
    assert a == b


def test_generate_id_same_title_different_whitespace():
    from execution.curation.id_gen import generate_id
    a = generate_id("  China  steel  output ")
    b = generate_id("China steel output")
    assert a == b


def test_generate_id_same_title_different_case():
    from execution.curation.id_gen import generate_id
    a = generate_id("EU Steel Deal")
    b = generate_id("eu steel deal")
    assert a == b


def test_generate_id_different_titles():
    from execution.curation.id_gen import generate_id
    a = generate_id("Title A")
    b = generate_id("Title B")
    assert a != b


def test_generate_id_length_is_12_hex():
    from execution.curation.id_gen import generate_id
    result = generate_id("sample title")
    assert len(result) == 12
    assert all(c in "0123456789abcdef" for c in result)


def test_generate_id_raises_on_empty():
    from execution.curation.id_gen import generate_id
    with pytest.raises(ValueError):
        generate_id("")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_curation_id_gen.py -v`
Expected: FAIL — `normalize_title` does not exist yet, `generate_id` signature is `(source, title)`.

- [ ] **Step 3: Implement normalize_title and new generate_id**

Replace the full contents of `execution/curation/id_gen.py` with:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_curation_id_gen.py -v`
Expected: all 16 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add execution/curation/id_gen.py tests/test_curation_id_gen.py
git commit -m "feat(dedup): title-only canonical ID with normalize_title"
```

---

### Task 2: Global seen-set in redis_client

**Files:**
- Modify: `execution/curation/redis_client.py`
- Modify: `tests/test_curation_redis_client.py`

- [ ] **Step 1: Add new tests to the test file**

Append the following tests to the end of `tests/test_curation_redis_client.py`:

```python
def test_seen_global_set_membership(fake_redis):
    """v2: is_seen/mark_seen use global sorted set (no date param)."""
    from execution.curation.redis_client import is_seen, mark_seen
    assert is_seen("abc123") is False
    mark_seen("abc123")
    assert is_seen("abc123") is True


def test_mark_seen_global_idempotent(fake_redis):
    """Calling mark_seen twice keeps ZCARD at 1."""
    from execution.curation.redis_client import mark_seen
    mark_seen("abc123")
    mark_seen("abc123")
    assert fake_redis.zcard("platts:seen") == 1


def test_mark_seen_global_prunes_old_entries(fake_redis):
    """Entries older than 30d are pruned on next mark_seen call."""
    import time
    from execution.curation.redis_client import mark_seen, is_seen
    old_ts = time.time() - (31 * 24 * 60 * 60)
    fake_redis.zadd("platts:seen", {"old_item": old_ts})
    mark_seen("new_item")
    assert is_seen("old_item") is False
    assert is_seen("new_item") is True


def test_staging_exists_true_after_set(fake_redis):
    from execution.curation.redis_client import set_staging, staging_exists
    assert staging_exists("abc123") is False
    set_staging("abc123", {"id": "abc123", "title": "Test"})
    assert staging_exists("abc123") is True


def test_staging_exists_false_after_discard(fake_redis):
    from execution.curation.redis_client import set_staging, discard, staging_exists
    set_staging("abc123", {"id": "abc123", "title": "Test"})
    discard("abc123")
    assert staging_exists("abc123") is False


def test_mark_scraped_populates_dated_set(fake_redis):
    from execution.curation.redis_client import mark_scraped
    mark_scraped("2026-04-16", "abc123")
    assert fake_redis.sismember("platts:scraped:2026-04-16", "abc123")


def test_mark_scraped_applies_30d_ttl(fake_redis):
    from execution.curation.redis_client import mark_scraped
    mark_scraped("2026-04-16", "abc123")
    ttl = fake_redis.ttl("platts:scraped:2026-04-16")
    assert 2591000 <= ttl <= 2592000


def test_mark_scraped_idempotent(fake_redis):
    from execution.curation.redis_client import mark_scraped
    mark_scraped("2026-04-16", "abc123")
    mark_scraped("2026-04-16", "abc123")
    assert fake_redis.scard("platts:scraped:2026-04-16") == 1
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `python -m pytest tests/test_curation_redis_client.py::test_seen_global_set_membership tests/test_curation_redis_client.py::test_staging_exists_true_after_set tests/test_curation_redis_client.py::test_mark_scraped_populates_dated_set -v`
Expected: FAIL — `is_seen` still requires `date`, `staging_exists` and `mark_scraped` don't exist.

- [ ] **Step 3: Implement global seen-set, staging_exists, mark_scraped**

Modify `execution/curation/redis_client.py`:

**Update the module docstring** (lines 1-8) — replace with:

```python
"""Redis keyspaces for Platts curation.

Keyspaces:
- platts:staging:<id>               JSON string, TTL 48h
- platts:archive:<date>:<id>        JSON string, no TTL (consumed by other project)
- platts:seen                       Sorted Set, score=epoch, rolling 30d dedup
- platts:scraped:<date>             Set of ids, TTL 30d (daily telemetry)
- platts:rationale:processed:<date> String flag, TTL 30h (1x/day gate)

All functions use REDIS_URL env var via _get_client(). Tests monkeypatch _get_client.
"""
```

**Replace `_seen_key` function** (line 60-61) with:

```python
_SEEN_KEY = "platts:seen"
```

**Replace `is_seen` function** (lines 127-130) with:

```python
def is_seen(item_id: str) -> bool:
    """Check if item id exists in global dedup sorted set."""
    client = _get_client()
    return client.zscore(_SEEN_KEY, item_id) is not None
```

**Replace `mark_seen` function** (lines 133-137) with:

```python
def mark_seen(item_id: str) -> None:
    """Add id to global dedup sorted set. Prunes entries older than 30d."""
    import time
    client = _get_client()
    now = time.time()
    try:
        client.zremrangebyscore(_SEEN_KEY, "-inf", now - _SEEN_TTL_SECONDS)
    except Exception:
        pass
    client.zadd(_SEEN_KEY, {item_id: now})
```

**Add after `mark_seen`:**

```python
def staging_exists(item_id: str) -> bool:
    """Check if a staging key exists for item_id."""
    client = _get_client()
    return bool(client.exists(_staging_key(item_id)))


def mark_scraped(date: str, item_id: str) -> None:
    """Add id to daily scraped set with 30d TTL. For /stats telemetry."""
    client = _get_client()
    key = f"platts:scraped:{date}"
    client.sadd(key, item_id)
    client.expire(key, _SEEN_TTL_SECONDS)
```

- [ ] **Step 4: Update old tests that use dated is_seen/mark_seen**

In `tests/test_curation_redis_client.py`, replace the two old tests:

Replace `test_seen_set_membership` (lines 76-81):
```python
def test_seen_set_membership(fake_redis):
    from execution.curation.redis_client import is_seen, mark_seen
    assert is_seen("abc123") is False
    mark_seen("abc123")
    assert is_seen("abc123") is True
```

Replace `test_mark_seen_applies_30d_ttl` (lines 83-88):
```python
def test_mark_seen_uses_sorted_set(fake_redis):
    from execution.curation.redis_client import mark_seen
    mark_seen("abc123")
    assert fake_redis.zscore("platts:seen", "abc123") is not None
```

- [ ] **Step 5: Run all redis_client tests**

Run: `python -m pytest tests/test_curation_redis_client.py -v`
Expected: all tests PASS (old updated + new ones).

- [ ] **Step 6: Commit**

```bash
git add execution/curation/redis_client.py tests/test_curation_redis_client.py
git commit -m "feat(dedup): global seen sorted set, staging_exists, mark_scraped"
```

---

### Task 3: Update router to use new dedup flow

**Files:**
- Modify: `execution/curation/router.py`
- Modify: `tests/test_curation_router.py`

- [ ] **Step 1: Add regression and short-circuit tests**

Append to `tests/test_curation_router.py`:

```python
def test_route_items_dedup_same_title_different_source(_redis):
    """H2 regression: same title in 'Latest' and 'Top News' must stage once."""
    from execution.curation.router import route_items
    items = [
        {"source": "Latest", "title": "EU reaches deal on steel", "tabName": ""},
        {"source": "Top News - Ferrous Metals", "title": "EU reaches deal on steel", "tabName": ""},
    ]
    counters, staged = route_items(
        items=items, today_date="2026-04-16", today_br="16/04/2026", logger=None,
    )
    assert counters["staged"] == 1
    assert counters["skipped_seen"] == 1
    assert len(staged) == 1


def test_route_items_dedup_across_days(_redis):
    """H1 regression: item seen yesterday must not re-stage today."""
    from execution.curation.router import route_items
    items = [{"source": "platts", "title": "Steel demand forecast", "tabName": "News"}]
    counters1, staged1 = route_items(
        items=items, today_date="2026-04-15", today_br="15/04/2026", logger=None,
    )
    assert counters1["staged"] == 1
    counters2, staged2 = route_items(
        items=items, today_date="2026-04-16", today_br="16/04/2026", logger=None,
    )
    assert counters2["staged"] == 0
    assert counters2["skipped_seen"] == 1


def test_route_items_staging_short_circuit(_redis):
    """Item already in staging (not yet archived) should be skipped."""
    from execution.curation.router import route_items
    from execution.curation import redis_client
    redis_client.set_staging("test_id", {"id": "test_id", "title": "Test"})
    from execution.curation.id_gen import generate_id
    title = "Test"
    item_id = generate_id(title)
    redis_client.set_staging(item_id, {"id": item_id, "title": title})
    items = [{"source": "platts", "title": title, "tabName": "News"}]
    counters, staged = route_items(
        items=items, today_date="2026-04-16", today_br="16/04/2026", logger=None,
    )
    assert counters["staged"] == 0
    assert counters["skipped_staged"] == 1


def test_route_items_counters_balance(_redis):
    """total == staged + skipped_seen + skipped_staged."""
    from execution.curation.router import route_items
    from execution.curation import redis_client
    from execution.curation.id_gen import generate_id
    items = [
        {"source": "platts", "title": "New article", "tabName": "News"},
        {"source": "platts", "title": "New article", "tabName": "News"},
        {"source": "platts", "title": "Staged one", "tabName": "News"},
    ]
    staged_id = generate_id("Staged one")
    redis_client.set_staging(staged_id, {"id": staged_id, "title": "Staged one"})
    counters, _ = route_items(
        items=items, today_date="2026-04-16", today_br="16/04/2026", logger=None,
    )
    assert counters["total"] == counters["staged"] + counters["skipped_seen"] + counters["skipped_staged"] + counters["skipped_invalid"]


def test_route_items_skips_empty_title(_redis):
    """Item with empty title should be skipped (generate_id raises ValueError)."""
    from execution.curation.router import route_items
    items = [
        {"source": "platts", "title": "", "tabName": "News"},
        {"source": "platts", "title": "Valid article", "tabName": "News"},
    ]
    counters, staged = route_items(
        items=items, today_date="2026-04-16", today_br="16/04/2026", logger=None,
    )
    assert counters["staged"] == 1
    assert len(staged) == 1
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `python -m pytest tests/test_curation_router.py::test_route_items_dedup_same_title_different_source -v`
Expected: FAIL — `generate_id` still expects 2 args in router.

- [ ] **Step 3: Update router.py**

Replace the full contents of `execution/curation/router.py` with:

```python
"""Classify dataset items and stage them in Redis with a `type` tag.

Post-v1.1: This module no longer posts to Telegram nor dispatches
rationale automatically. It stages everything in `platts:staging:*`
with a `type` field (`"news"` | `"rationale"`) so the caller can
send a single ingestion digest and the operator can review each item
manually via /queue.
"""
import re
from typing import List, Optional, Tuple

from execution.core.logger import WorkflowLogger
from execution.curation import redis_client
from execution.curation.id_gen import generate_id

_RATIONALE_TAB_RE = re.compile(r"\b(Rationale|Lump)\b", re.IGNORECASE)


def classify(item: dict) -> str:
    """Return 'rationale' for RMW Rationale/Lump items, 'curation' otherwise."""
    source = item.get("source") or ""
    tab_name = item.get("tabName") or ""
    if source.startswith("rmw") and _RATIONALE_TAB_RE.search(tab_name):
        return "rationale"
    return "curation"


def _type_tag(item: dict) -> str:
    """Return the staging type tag: 'rationale' → 'rationale', else 'news'."""
    return "rationale" if classify(item) == "rationale" else "news"


def _stage_only(item: dict, item_id: str, item_type: str, today_date: str) -> dict:
    """Stage one item in Redis and mark seen + scraped. Returns the dict that was staged."""
    to_stage = {**item, "id": item_id, "type": item_type}
    redis_client.set_staging(item_id, to_stage)
    redis_client.mark_seen(item_id)
    redis_client.mark_scraped(today_date, item_id)
    return to_stage


def route_items(
    items: List[dict],
    today_date: str,
    today_br: str,
    logger: Optional[WorkflowLogger] = None,
) -> Tuple[dict, List[dict]]:
    """Classify + stage every dataset item. Returns (counters, staged_items).

    counters keys: total, staged, news_staged, rationale_staged, skipped_seen,
    skipped_staged, skipped_invalid.
    staged_items: list of dicts actually written to Redis.
    """
    log = logger or WorkflowLogger("CurationRouter")
    counters = {
        "total": len(items),
        "staged": 0,
        "news_staged": 0,
        "rationale_staged": 0,
        "skipped_seen": 0,
        "skipped_staged": 0,
        "skipped_invalid": 0,
    }
    staged: List[dict] = []

    for item in items:
        item_type = _type_tag(item)
        try:
            item_id = generate_id(item.get("title", ""))
        except ValueError:
            counters["skipped_invalid"] += 1
            log.warning(f"Skipped item with empty/invalid title: {item.get('source', '?')}")
            continue
        if redis_client.staging_exists(item_id):
            counters["skipped_staged"] += 1
            continue
        if redis_client.is_seen(item_id):
            counters["skipped_seen"] += 1
            continue
        staged_item = _stage_only(item, item_id, item_type, today_date)
        staged.append(staged_item)
        counters["staged"] += 1
        if item_type == "rationale":
            counters["rationale_staged"] += 1
        else:
            counters["news_staged"] += 1

    log.info(f"Staged {counters['staged']} items "
             f"({counters['news_staged']} news, {counters['rationale_staged']} rationale); "
             f"{counters['skipped_seen']} skipped as seen, "
             f"{counters['skipped_staged']} skipped in staging, "
             f"{counters['skipped_invalid']} skipped invalid")
    return counters, staged
```

- [ ] **Step 4: Update existing router test that used old generate_id**

In `tests/test_curation_router.py`, replace `test_route_items_respects_is_seen_dedup` (lines 57-70):

```python
def test_route_items_respects_is_seen_dedup(_redis):
    from execution.curation.router import route_items
    from execution.curation import redis_client
    from execution.curation.id_gen import generate_id
    item = {"source": "platts", "title": "Duplicated", "tabName": "News"}
    item_id = generate_id("Duplicated")
    redis_client.mark_seen(item_id)
    counters, staged = route_items(
        items=[item], today_date="2026-04-15", today_br="15/04/2026",
        logger=None,
    )
    assert counters["skipped_seen"] == 1
    assert counters["staged"] == 0
    assert staged == []
```

- [ ] **Step 5: Run all router tests**

Run: `python -m pytest tests/test_curation_router.py -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add execution/curation/router.py tests/test_curation_router.py
git commit -m "feat(dedup): router uses title-only ID + staging short-circuit"
```

---

### Task 4: Update stats_for_date to use platts:scraped

**Files:**
- Modify: `webhook/redis_queries.py:196-226`
- Modify: `tests/test_redis_queries.py`

- [ ] **Step 1: Update the stats test**

In `tests/test_redis_queries.py`, replace `test_stats_for_date_populated` (lines 172-196):

```python
def test_stats_for_date_populated(fake_redis):
    """Uses today's UTC date because save_feedback timestamps with time.time()."""
    from webhook.redis_queries import stats_for_date, save_feedback, mark_pipeline_processed
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    other_day = "2020-01-01"
    # scraped: 3 in scraped set (new v2 key)
    fake_redis.sadd(f"platts:scraped:{today}", "a", "b", "c")
    # staging: 2
    fake_redis.set("platts:staging:s1", json.dumps({"id": "s1"}))
    fake_redis.set("platts:staging:s2", json.dumps({"id": "s2"}))
    # archived: 4 today
    for i in range(4):
        fake_redis.set(f"platts:archive:{today}:x{i}", json.dumps({"id": f"x{i}"}))
    # archived: 1 on a different date (should not count)
    fake_redis.set(f"platts:archive:{other_day}:y", json.dumps({"id": "y"}))
    # rejected: 2 today
    save_feedback("curate_reject", "r1", 1, "", "T1")
    save_feedback("draft_reject", "r2", 1, "", "T2")
    # pipeline: 2
    mark_pipeline_processed("p1", today)
    mark_pipeline_processed("p2", today)

    stats = stats_for_date(today)
    assert stats == {"scraped": 3, "staging": 2, "archived": 4, "rejected": 2, "pipeline": 2}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_redis_queries.py::test_stats_for_date_populated -v`
Expected: FAIL — `stats_for_date` still reads `platts:seen:<date>` (old key), not `platts:scraped:<date>`.

- [ ] **Step 3: Update stats_for_date**

In `webhook/redis_queries.py`, replace line 204:

```python
    scraped = client.scard(f"platts:seen:{date_iso}")
```

with:

```python
    scraped = client.scard(f"platts:scraped:{date_iso}")
```

Also update the module docstring — replace line 10:

```
- platts:seen:<date>                (read, for stats)
```

with:

```
- platts:scraped:<date>             (read, for stats — daily telemetry set)
```

- [ ] **Step 4: Run all redis_queries tests**

Run: `python -m pytest tests/test_redis_queries.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: all tests PASS. This is the checkpoint where all production code changes are validated together.

- [ ] **Step 6: Commit**

```bash
git add webhook/redis_queries.py tests/test_redis_queries.py
git commit -m "fix(stats): read platts:scraped instead of platts:seen for daily count"
```

---

### Task 5: Migration script

**Files:**
- Create: `execution/scripts/rebuild_dedup.py`
- Create: `tests/test_rebuild_dedup.py`

- [ ] **Step 1: Write tests for migration script**

Create `tests/test_rebuild_dedup.py`:

```python
"""Tests for execution.scripts.rebuild_dedup migration."""
import json
import pytest
import fakeredis


@pytest.fixture
def fake_redis(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    from execution.curation import redis_client
    monkeypatch.setattr(redis_client, "_get_client", lambda: fake)
    monkeypatch.setattr(redis_client, "_client", None)
    return fake


def test_dry_run_does_not_mutate(fake_redis):
    from execution.scripts.rebuild_dedup import rebuild
    fake_redis.set("platts:archive:2026-04-14:old_id", json.dumps({
        "title": "Test article",
        "archivedAt": "2026-04-14T10:00:00+00:00",
    }))
    fake_redis.sadd("platts:seen:2026-04-14", "old_id")
    result = rebuild(fake_redis, dry_run=True)
    assert result["unique_ids"] >= 1
    assert fake_redis.exists("platts:seen:2026-04-14") == 1
    assert fake_redis.zcard("platts:seen") == 0


def test_execute_populates_global_seen(fake_redis):
    from execution.scripts.rebuild_dedup import rebuild
    fake_redis.set("platts:archive:2026-04-14:old_id", json.dumps({
        "title": "Test article",
        "archivedAt": "2026-04-14T10:00:00+00:00",
    }))
    result = rebuild(fake_redis, dry_run=False)
    assert result["unique_ids"] >= 1
    assert fake_redis.zcard("platts:seen") == result["unique_ids"]


def test_execute_deletes_dated_seen_keys(fake_redis):
    from execution.scripts.rebuild_dedup import rebuild
    fake_redis.sadd("platts:seen:2026-04-14", "a", "b")
    fake_redis.sadd("platts:seen:2026-04-15", "c")
    fake_redis.set("platts:archive:2026-04-14:a", json.dumps({"title": "A"}))
    rebuild(fake_redis, dry_run=False)
    assert fake_redis.exists("platts:seen:2026-04-14") == 0
    assert fake_redis.exists("platts:seen:2026-04-15") == 0


def test_execute_idempotent(fake_redis):
    from execution.scripts.rebuild_dedup import rebuild
    fake_redis.set("platts:archive:2026-04-14:old_id", json.dumps({
        "title": "Test article",
    }))
    result1 = rebuild(fake_redis, dry_run=False)
    result2 = rebuild(fake_redis, dry_run=False)
    assert result1["unique_ids"] == result2["unique_ids"]
    assert fake_redis.zcard("platts:seen") == result1["unique_ids"]


def test_execute_deduplicates_same_title_different_ids(fake_redis):
    from execution.scripts.rebuild_dedup import rebuild
    fake_redis.set("platts:archive:2026-04-14:id_a", json.dumps({
        "title": "EU steel deal",
    }))
    fake_redis.set("platts:archive:2026-04-15:id_b", json.dumps({
        "title": "EU steel deal",
    }))
    result = rebuild(fake_redis, dry_run=False)
    assert result["archive_count"] == 2
    assert result["unique_ids"] == 1
    assert fake_redis.zcard("platts:seen") == 1


def test_execute_skips_missing_title(fake_redis):
    from execution.scripts.rebuild_dedup import rebuild
    fake_redis.set("platts:archive:2026-04-14:no_title", json.dumps({
        "something": "else",
    }))
    fake_redis.set("platts:archive:2026-04-14:ok", json.dumps({
        "title": "Valid",
    }))
    result = rebuild(fake_redis, dry_run=False)
    assert result["skipped"] == 1
    assert result["unique_ids"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_rebuild_dedup.py -v`
Expected: FAIL — `execution.scripts.rebuild_dedup` module does not exist.

- [ ] **Step 3: Implement migration script**

Create `execution/scripts/rebuild_dedup.py`:

```python
#!/usr/bin/env python3
"""One-shot migration: rebuild global platts:seen from archives.

Usage:
    python execution/scripts/rebuild_dedup.py           # dry-run (default)
    python execution/scripts/rebuild_dedup.py --execute  # apply changes
"""
import argparse
import json
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from execution.curation.id_gen import generate_id


def rebuild(client, dry_run: bool = True) -> dict:
    """Rebuild global seen set from all archives.

    Returns dict with stats: archive_count, unique_ids, skipped, dated_keys_deleted.
    """
    archive_keys = list(client.scan_iter(match="platts:archive:*", count=500))
    now = time.time()
    new_ids: set[str] = set()
    skipped = 0

    for key in archive_keys:
        raw = client.get(key)
        if raw is None:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            skipped += 1
            continue
        title = data.get("title", "")
        if not title or not title.strip():
            skipped += 1
            continue
        try:
            new_id = generate_id(title)
            new_ids.add(new_id)
        except ValueError:
            skipped += 1

    dated_keys = list(client.scan_iter(match="platts:seen:????-??-??", count=100))

    result = {
        "archive_count": len(archive_keys),
        "unique_ids": len(new_ids),
        "skipped": skipped,
        "dated_keys_found": len(dated_keys),
    }

    if dry_run:
        print(f"[DRY RUN] Would ZADD {len(new_ids)} IDs into platts:seen")
        print(f"[DRY RUN] Would DEL {len(dated_keys)} dated seen keys: {dated_keys}")
        print(f"[DRY RUN] Archives scanned: {len(archive_keys)}, skipped: {skipped}")
        return result

    if new_ids:
        pipe = client.pipeline()
        for new_id in new_ids:
            pipe.zadd("platts:seen", {new_id: now})
        pipe.execute()

    for key in dated_keys:
        client.delete(key)

    print(f"[DONE] ZADD {len(new_ids)} IDs into platts:seen")
    print(f"[DONE] DEL {len(dated_keys)} dated seen keys")
    print(f"[DONE] Archives scanned: {len(archive_keys)}, skipped: {skipped}")
    return result


def main():
    parser = argparse.ArgumentParser(description="Rebuild global platts:seen from archives")
    parser.add_argument("--execute", action="store_true", help="Apply changes (default is dry-run)")
    args = parser.parse_args()

    from execution.curation import redis_client
    client = redis_client._get_client()
    rebuild(client, dry_run=not args.execute)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_rebuild_dedup.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: all tests PASS (existing + new).

- [ ] **Step 6: Commit**

```bash
git add execution/scripts/rebuild_dedup.py tests/test_rebuild_dedup.py
git commit -m "feat(dedup): migration script to rebuild global seen set from archives"
```

---

### Task 6: Manual production validation

**Files:** None — operational steps only.

- [ ] **Step 1: Push to main**

```bash
git push origin main
```

Wait for Railway deploy to succeed. Check via: `railway deployment list --service web | head -5`

- [ ] **Step 2: Run migration dry-run**

```bash
railway run --service web -- python execution/scripts/rebuild_dedup.py
```

Verify output shows: `Would ZADD N IDs`, `Would DEL K dated seen keys`. N should be close to the unique archive count (~30-40 based on current data).

- [ ] **Step 3: Run migration execute**

```bash
railway run --service web -- python execution/scripts/rebuild_dedup.py --execute
```

Verify output shows: `ZADD N IDs into platts:seen`, `DEL K dated seen keys`.

- [ ] **Step 4: Verify Redis state**

```bash
redis-cli -u '<REDIS_PUBLIC_URL>' ZCARD platts:seen
```

Expected: matches the `unique_ids` count from the migration output.

```bash
redis-cli -u '<REDIS_PUBLIC_URL>' EXISTS platts:seen:2026-04-14
redis-cli -u '<REDIS_PUBLIC_URL>' EXISTS platts:seen:2026-04-15
redis-cli -u '<REDIS_PUBLIC_URL>' EXISTS platts:seen:2026-04-16
```

Expected: all return `0` (dated keys deleted).

- [ ] **Step 5: Trigger manual Apify scrape**

Go to Apify console and start the `platts-scrap-full-news` actor manually. Wait for completion.

Expected: digest in Telegram shows near-zero new items (most articles already "seen" from migration). If any new articles were published since last scrape, those should appear.

- [ ] **Step 6: Verify /stats**

Send `/stats` in Telegram bot. Expected: `🔎 Scraped` counter shows the number from the manual scrape run (should be small since most were skipped).

- [ ] **Step 7: Wait for next day's automatic scrape**

After the next scheduled scrape run (9h/12h/15h BRT):
- Check digest: only genuinely new articles should appear
- No duplicates of yesterday's news
- `/stats` shows correct scraped count for the day
