# Bot Navigation v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adicionar 5 comandos de navegação/discovery ao bot Telegram (`/help`, `/queue`, `/history`, `/stats`, `/rejections`) com captura opcional de razão em recusas, registro de comandos via `setMyCommands` e consulta direta do Redis.

**Architecture:** Dois módulos novos (`webhook/redis_queries.py` puro I/O, `webhook/query_handlers.py` formatação). Toque mínimo em `webhook/app.py` — só dispatch dos comandos novos e um state dict pra captura de razão. TDD com `fakeredis` seguindo o padrão de `tests/test_curation_redis_client.py`.

**Tech Stack:** Python 3.11 · Flask · redis-py · fakeredis · pytest

**Spec:** `docs/superpowers/specs/2026-04-14-bot-navigation-v1-design.md`

**Gotcha crítico do ambiente:** Path do projeto tem trailing space. Sempre: `cd /Users/bigode/Dev/Antigravity\ WF\ `.

---

## Arquivos-alvo

### Criar
- `webhook/redis_queries.py` — I/O Redis puro (list_staging, list_archive_recent, stats_for_date, save_feedback, update_feedback_reason, list_feedback, mark_pipeline_processed)
- `webhook/query_handlers.py` — formatação dos 5 comandos + callback `queue_open:<id>` + `queue_page:<n>`
- `tests/test_redis_queries.py` — ~16 testes
- `tests/test_query_handlers.py` — ~11 testes
- `tests/test_reject_reason_flow.py` — ~7 testes

### Modificar
- `execution/curation/redis_client.py` — `set_staging` injeta `stagedAt` (Task 0)
- `tests/test_curation_redis_client.py` — assert `stagedAt` presente
- `webhook/app.py` — 5 dispatches em `handle_message`, `REJECT_REASON_STATE` dict, cascade check antes do `ADJUST_STATE`, alteração em `reject` e `curate_reject` handlers, alteração em `curate_pipeline` (chama `mark_pipeline_processed`), rota `POST /admin/register-commands`

---

## Task 0: Inject stagedAt timestamp on set_staging

**Contexto:** `_stage_and_post` em `execution/curation/router.py:39` chama `set_staging` com um dict que só tem `id` injetado — nenhum timestamp de "quando entrou em staging". Spec exige `/queue` ordenado por mais novo primeiro. Sem esse campo o sort cai em string vazia. Esta task fecha a base antes do `list_staging` ser implementado.

**Files:**
- Modify: `execution/curation/redis_client.py` (`set_staging`)
- Modify: `tests/test_curation_redis_client.py` (assertion)

- [ ] **Step 1: Update test to require stagedAt**

In `tests/test_curation_redis_client.py`, locate the existing `test_set_staging_persists_json` (or equivalent) and append:

```python
def test_set_staging_injects_staged_at(fake_redis):
    """set_staging stamps stagedAt UTC ISO8601 so /queue can sort newest-first."""
    import json
    from datetime import datetime, timezone
    from execution.curation.redis_client import set_staging
    set_staging("abc123", {"id": "abc123", "title": "T"})
    raw = fake_redis.get("platts:staging:abc123")
    data = json.loads(raw)
    assert "stagedAt" in data
    # Parse and confirm it's a valid recent UTC timestamp
    parsed = datetime.fromisoformat(data["stagedAt"].replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    delta = abs((datetime.now(timezone.utc) - parsed).total_seconds())
    assert delta < 5


def test_set_staging_preserves_existing_staged_at(fake_redis):
    """If caller already set stagedAt (e.g., reprocess flow), do not overwrite."""
    import json
    from execution.curation.redis_client import set_staging
    fixed = "2026-01-01T00:00:00+00:00"
    set_staging("abc123", {"id": "abc123", "stagedAt": fixed})
    data = json.loads(fake_redis.get("platts:staging:abc123"))
    assert data["stagedAt"] == fixed
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_curation_redis_client.py::test_set_staging_injects_staged_at -v`
Expected: FAIL with `KeyError: 'stagedAt'` (or AssertionError).

- [ ] **Step 3: Implement stagedAt injection**

In `execution/curation/redis_client.py`, replace `set_staging`:

```python
def set_staging(item_id: str, item: dict) -> None:
    """Persist item as JSON with 48h TTL.

    Injects stagedAt (UTC ISO8601) if not already present. The caller
    can pre-set it (e.g., reprocess flow that wants to preserve original
    staging time) and we will not overwrite.
    """
    item = dict(item)
    item.setdefault("stagedAt", datetime.now(timezone.utc).isoformat())
    client = _get_client()
    client.set(_staging_key(item_id), json.dumps(item, ensure_ascii=False), ex=_STAGING_TTL_SECONDS)
```

`datetime` and `timezone` are already imported at the top of the file (used by `archive`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_curation_redis_client.py -v`
Expected: all existing tests pass + 2 new ones pass.

- [ ] **Step 5: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF "
git add execution/curation/redis_client.py tests/test_curation_redis_client.py
git commit -m "feat(curation): inject stagedAt timestamp in set_staging

Required for Bot Navigation v1 /queue command which sorts staging
items newest-first. Idempotent: respects pre-set stagedAt so reprocess
flows can preserve original time."
```

---

## Task 1: redis_queries skeleton + list_staging

**Files:**
- Create: `webhook/redis_queries.py`
- Create: `tests/test_redis_queries.py`

- [ ] **Step 1: Create module skeleton**

Create `webhook/redis_queries.py`:

```python
"""Redis query helpers for webhook bot navigation and feedback.

Complements execution.curation.redis_client by adding read-side queries
(list, count, stats) and a new feedback keyspace. Kept separate from
curation to preserve contact_admin.py-style modularity.

Keyspaces used:
- platts:staging:<id>               (read)
- platts:archive:<date>:<id>        (read)
- platts:seen:<date>                (read, for stats)
- platts:pipeline:processed:<date>  (read + write, new)
- webhook:feedback:<ts>-<id>        (read + write, new Hash)
- webhook:feedback:index            (read + write, new Sorted Set)

All functions take an injectable redis client via _get_client() so tests
can swap in fakeredis. Same pattern as execution.curation.redis_client.
"""
import json
import os
import time
from typing import Optional

_FEEDBACK_TTL_SECONDS = 30 * 24 * 60 * 60   # 30 days
_PIPELINE_TTL_SECONDS = 2 * 24 * 60 * 60    # 2 days

_client = None


def _get_client():
    """Return a cached Redis client using REDIS_URL."""
    global _client
    if _client is not None:
        return _client
    import redis
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        raise RuntimeError("REDIS_URL env var not set")
    _client = redis.Redis.from_url(
        url,
        socket_connect_timeout=3,
        socket_timeout=3,
        decode_responses=True,
    )
    return _client


def list_staging(limit: int = 50) -> list[dict]:
    """Return staging items newest-first, up to limit.

    Each dict contains the full parsed JSON plus an 'id' field extracted
    from the key suffix (in case the stored payload lacks it).
    """
    client = _get_client()
    keys = list(client.scan_iter(match="platts:staging:*", count=200))
    items: list[dict] = []
    for key in keys:
        raw = client.get(key)
        if raw is None:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        item_id = key.rsplit(":", 1)[-1]
        data.setdefault("id", item_id)
        items.append(data)
    items.sort(key=lambda d: d.get("stagedAt") or d.get("createdAt") or "", reverse=True)
    return items[:limit]
```

- [ ] **Step 2: Create test file with first failing test**

Create `tests/test_redis_queries.py`:

```python
"""Tests for webhook.redis_queries."""
import json
import time
import pytest
import fakeredis


@pytest.fixture
def fake_redis(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    from webhook import redis_queries
    monkeypatch.setattr(redis_queries, "_get_client", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _reset_client_cache(monkeypatch):
    from webhook import redis_queries
    monkeypatch.setattr(redis_queries, "_client", None)


def test_list_staging_empty(fake_redis):
    from webhook.redis_queries import list_staging
    assert list_staging() == []


def test_list_staging_sorted_newest_first(fake_redis):
    from webhook.redis_queries import list_staging
    fake_redis.set("platts:staging:a", json.dumps({"id": "a", "title": "A", "stagedAt": "2026-04-15T10:00:00Z"}))
    fake_redis.set("platts:staging:b", json.dumps({"id": "b", "title": "B", "stagedAt": "2026-04-15T12:00:00Z"}))
    fake_redis.set("platts:staging:c", json.dumps({"id": "c", "title": "C", "stagedAt": "2026-04-15T11:00:00Z"}))
    result = list_staging()
    assert [d["id"] for d in result] == ["b", "c", "a"]


def test_list_staging_respects_limit(fake_redis):
    from webhook.redis_queries import list_staging
    for i in range(5):
        fake_redis.set(f"platts:staging:item{i}", json.dumps({"id": f"item{i}", "stagedAt": f"2026-04-15T{i:02d}:00:00Z"}))
    result = list_staging(limit=3)
    assert len(result) == 3


def test_list_staging_skips_malformed_json(fake_redis):
    from webhook.redis_queries import list_staging
    fake_redis.set("platts:staging:good", json.dumps({"id": "good", "title": "ok"}))
    fake_redis.set("platts:staging:bad", "not-json{{{")
    result = list_staging()
    assert len(result) == 1
    assert result[0]["id"] == "good"


def test_list_staging_fills_id_from_key(fake_redis):
    """If the stored JSON lacks 'id', we derive it from the key suffix."""
    from webhook.redis_queries import list_staging
    fake_redis.set("platts:staging:abc123", json.dumps({"title": "no id field"}))
    result = list_staging()
    assert result[0]["id"] == "abc123"
```

- [ ] **Step 3: Run tests to verify they pass (implementation already complete)**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_redis_queries.py -v`
Expected: 5 passing.

- [ ] **Step 4: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF "
git add webhook/redis_queries.py tests/test_redis_queries.py
git commit -m "feat(queries): list_staging with fakeredis tests"
```

---

## Task 2: list_archive_recent

**Files:**
- Modify: `webhook/redis_queries.py` (add function)
- Modify: `tests/test_redis_queries.py` (add tests)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_redis_queries.py`:

```python
def test_list_archive_recent_empty(fake_redis):
    from webhook.redis_queries import list_archive_recent
    assert list_archive_recent() == []


def test_list_archive_recent_crossdate_sorted(fake_redis):
    from webhook.redis_queries import list_archive_recent
    fake_redis.set("platts:archive:2026-04-13:x", json.dumps({"id": "x", "title": "X", "archivedAt": "2026-04-13T09:00:00+00:00"}))
    fake_redis.set("platts:archive:2026-04-15:y", json.dumps({"id": "y", "title": "Y", "archivedAt": "2026-04-15T14:00:00+00:00"}))
    fake_redis.set("platts:archive:2026-04-14:z", json.dumps({"id": "z", "title": "Z", "archivedAt": "2026-04-14T11:00:00+00:00"}))
    result = list_archive_recent(limit=10)
    assert [d["id"] for d in result] == ["y", "z", "x"]


def test_list_archive_recent_respects_limit(fake_redis):
    from webhook.redis_queries import list_archive_recent
    for i in range(15):
        ts = f"2026-04-15T{i:02d}:00:00+00:00"
        fake_redis.set(f"platts:archive:2026-04-15:i{i}", json.dumps({"id": f"i{i}", "archivedAt": ts}))
    result = list_archive_recent(limit=10)
    assert len(result) == 10


def test_list_archive_recent_derives_date_from_key(fake_redis):
    """Each dict should have archived_date extracted from key middle segment."""
    from webhook.redis_queries import list_archive_recent
    fake_redis.set("platts:archive:2026-04-15:abc", json.dumps({"id": "abc", "archivedAt": "2026-04-15T10:00:00+00:00"}))
    result = list_archive_recent()
    assert result[0]["archived_date"] == "2026-04-15"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_redis_queries.py::test_list_archive_recent_empty -v`
Expected: FAIL with `ImportError: cannot import name 'list_archive_recent'`.

- [ ] **Step 3: Implement**

Append to `webhook/redis_queries.py`:

```python
def list_archive_recent(limit: int = 10) -> list[dict]:
    """Return archived items newest-first across all dates, up to limit.

    Each dict contains the parsed JSON plus 'id' and 'archived_date'
    derived from the key structure platts:archive:<date>:<id>.
    """
    client = _get_client()
    keys = list(client.scan_iter(match="platts:archive:*", count=500))
    items: list[dict] = []
    for key in keys:
        raw = client.get(key)
        if raw is None:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        parts = key.split(":")
        if len(parts) < 4:
            continue
        archived_date = parts[2]
        item_id = parts[3]
        data.setdefault("id", item_id)
        data["archived_date"] = archived_date
        items.append(data)
    items.sort(key=lambda d: d.get("archivedAt") or "", reverse=True)
    return items[:limit]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_redis_queries.py -v`
Expected: 9 passing.

- [ ] **Step 5: Commit**

```bash
git add webhook/redis_queries.py tests/test_redis_queries.py
git commit -m "feat(queries): list_archive_recent crossdate sorting"
```

---

## Task 3: feedback save/update/list

**Files:**
- Modify: `webhook/redis_queries.py`
- Modify: `tests/test_redis_queries.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_redis_queries.py`:

```python
def test_save_feedback_creates_hash_and_index(fake_redis):
    from webhook.redis_queries import save_feedback
    key = save_feedback("curate_reject", "abc123", 999, "", "Sample title")
    assert key.endswith("-abc123")
    data = fake_redis.hgetall(f"webhook:feedback:{key}")
    assert data["action"] == "curate_reject"
    assert data["item_id"] == "abc123"
    assert data["chat_id"] == "999"
    assert data["reason"] == ""
    assert data["title"] == "Sample title"
    assert float(data["timestamp"]) > 0
    assert fake_redis.zscore("webhook:feedback:index", key) is not None


def test_save_feedback_empty_reason_allowed(fake_redis):
    from webhook.redis_queries import save_feedback
    key = save_feedback("draft_reject", "draft42", 999, "", "Draft title")
    data = fake_redis.hgetall(f"webhook:feedback:{key}")
    assert data["reason"] == ""


def test_save_feedback_applies_30d_ttl(fake_redis):
    from webhook.redis_queries import save_feedback
    key = save_feedback("curate_reject", "x", 1, "", "T")
    ttl = fake_redis.ttl(f"webhook:feedback:{key}")
    assert 30 * 24 * 3600 - 10 <= ttl <= 30 * 24 * 3600


def test_update_feedback_reason_updates_hash(fake_redis):
    from webhook.redis_queries import save_feedback, update_feedback_reason
    key = save_feedback("curate_reject", "xyz", 1, "", "T")
    updated = update_feedback_reason(key, "duplicate of item foo")
    assert updated is True
    data = fake_redis.hgetall(f"webhook:feedback:{key}")
    assert data["reason"] == "duplicate of item foo"


def test_update_feedback_reason_nonexistent_returns_false(fake_redis):
    from webhook.redis_queries import update_feedback_reason
    assert update_feedback_reason("1234567890-doesnotexist", "whatever") is False


def test_list_feedback_most_recent_first(fake_redis):
    from webhook.redis_queries import save_feedback, list_feedback
    key_a = save_feedback("curate_reject", "a", 1, "reason a", "Title A")
    time.sleep(0.01)
    key_b = save_feedback("curate_reject", "b", 1, "reason b", "Title B")
    time.sleep(0.01)
    key_c = save_feedback("draft_reject", "c", 1, "reason c", "Title C")
    results = list_feedback(limit=10)
    assert [r["item_id"] for r in results] == ["c", "b", "a"]


def test_list_feedback_filter_by_action(fake_redis):
    from webhook.redis_queries import save_feedback, list_feedback
    save_feedback("curate_reject", "a", 1, "", "A")
    save_feedback("draft_reject", "b", 1, "", "B")
    save_feedback("curate_reject", "c", 1, "", "C")
    results = list_feedback(limit=10, action="curate_reject")
    assert len(results) == 2
    assert all(r["action"] == "curate_reject" for r in results)


def test_list_feedback_filter_since_ts(fake_redis):
    from webhook.redis_queries import save_feedback, list_feedback
    save_feedback("curate_reject", "old", 1, "", "Old")
    time.sleep(0.05)
    cutoff = time.time()
    time.sleep(0.05)
    save_feedback("curate_reject", "new", 1, "", "New")
    results = list_feedback(limit=10, since_ts=cutoff)
    assert [r["item_id"] for r in results] == ["new"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_redis_queries.py::test_save_feedback_creates_hash_and_index -v`
Expected: FAIL with `ImportError: cannot import name 'save_feedback'`.

- [ ] **Step 3: Implement**

Append to `webhook/redis_queries.py`:

```python
def _feedback_key(feedback_id: str) -> str:
    return f"webhook:feedback:{feedback_id}"


def save_feedback(action: str, item_id: str, chat_id: int,
                  reason: str, title: str) -> str:
    """Create feedback Hash + index entry. Returns feedback_id '<ts>-<item_id>'."""
    client = _get_client()
    ts = time.time()
    feedback_id = f"{ts:.3f}-{item_id}"
    full_key = _feedback_key(feedback_id)
    pipe = client.pipeline(transaction=True)
    pipe.hset(full_key, mapping={
        "action": action,
        "item_id": item_id,
        "chat_id": str(chat_id),
        "reason": reason or "",
        "timestamp": f"{ts:.3f}",
        "title": title or "",
    })
    pipe.expire(full_key, _FEEDBACK_TTL_SECONDS)
    pipe.zadd("webhook:feedback:index", {feedback_id: ts})
    pipe.execute()
    return feedback_id


def update_feedback_reason(feedback_id: str, reason: str) -> bool:
    """Update reason field of an existing feedback Hash.

    Returns True if updated, False if the key doesn't exist.
    """
    client = _get_client()
    full_key = _feedback_key(feedback_id)
    if not client.exists(full_key):
        return False
    client.hset(full_key, "reason", reason or "")
    return True


def list_feedback(limit: int = 10,
                  action: Optional[str] = None,
                  since_ts: Optional[float] = None) -> list[dict]:
    """List feedback entries newest-first with optional filters.

    action: exact match filter on the 'action' field.
    since_ts: lower bound (inclusive) on the epoch timestamp.
    """
    client = _get_client()
    members = client.zrevrange("webhook:feedback:index", 0, -1, withscores=True)
    results: list[dict] = []
    for feedback_id, score in members:
        if since_ts is not None and score < since_ts:
            continue
        data = client.hgetall(_feedback_key(feedback_id))
        if not data:
            continue
        if action is not None and data.get("action") != action:
            continue
        data["feedback_id"] = feedback_id
        try:
            data["timestamp"] = float(data.get("timestamp") or 0)
        except ValueError:
            data["timestamp"] = 0.0
        try:
            data["chat_id"] = int(data.get("chat_id") or 0)
        except ValueError:
            data["chat_id"] = 0
        results.append(data)
        if len(results) >= limit:
            break
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_redis_queries.py -v`
Expected: 17 passing.

- [ ] **Step 5: Commit**

```bash
git add webhook/redis_queries.py tests/test_redis_queries.py
git commit -m "feat(queries): feedback save/update/list with sorted-set index"
```

---

## Task 4: stats_for_date + mark_pipeline_processed

**Files:**
- Modify: `webhook/redis_queries.py`
- Modify: `tests/test_redis_queries.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_redis_queries.py`:

```python
def test_stats_for_date_all_zero(fake_redis):
    from webhook.redis_queries import stats_for_date
    stats = stats_for_date("2026-04-15")
    assert stats == {"scraped": 0, "staging": 0, "archived": 0, "rejected": 0, "pipeline": 0}


def test_stats_for_date_populated(fake_redis):
    """Uses today's UTC date because save_feedback timestamps with time.time()."""
    from webhook.redis_queries import stats_for_date, save_feedback, mark_pipeline_processed
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    other_day = "2020-01-01"
    # scraped: 3 in seen set
    fake_redis.sadd(f"platts:seen:{today}", "a", "b", "c")
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


def test_stats_rejected_only_counts_reject_actions(fake_redis):
    """Future feedback actions (e.g., 'adjust') must NOT inflate rejected count.

    Spec: rejected = entries with action in {'curate_reject', 'draft_reject'}.
    """
    from webhook.redis_queries import stats_for_date, save_feedback
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    save_feedback("curate_reject", "a", 1, "", "T")
    save_feedback("draft_reject", "b", 1, "", "T")
    save_feedback("adjust", "c", 1, "", "T")          # not a rejection
    save_feedback("approve", "d", 1, "", "T")         # not a rejection
    stats = stats_for_date(today)
    assert stats["rejected"] == 2


def test_mark_pipeline_processed_idempotent(fake_redis):
    from webhook.redis_queries import mark_pipeline_processed
    mark_pipeline_processed("x", "2026-04-15")
    mark_pipeline_processed("x", "2026-04-15")
    assert fake_redis.scard("platts:pipeline:processed:2026-04-15") == 1


def test_mark_pipeline_processed_applies_ttl(fake_redis):
    from webhook.redis_queries import mark_pipeline_processed
    mark_pipeline_processed("x", "2026-04-15")
    ttl = fake_redis.ttl("platts:pipeline:processed:2026-04-15")
    assert 2 * 24 * 3600 - 10 <= ttl <= 2 * 24 * 3600
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_redis_queries.py::test_stats_for_date_all_zero -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement**

Append to `webhook/redis_queries.py`:

```python
def mark_pipeline_processed(item_id: str, date_iso: str) -> None:
    """Add item_id to the daily pipeline-processed set with 2d TTL."""
    client = _get_client()
    key = f"platts:pipeline:processed:{date_iso}"
    client.sadd(key, item_id)
    client.expire(key, _PIPELINE_TTL_SECONDS)


def _date_bounds_epoch(date_iso: str) -> tuple[float, float]:
    """Return (start, end) epoch seconds for the UTC day matching date_iso."""
    from datetime import datetime, timezone, timedelta
    start_dt = datetime.strptime(date_iso, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = start_dt + timedelta(days=1)
    return start_dt.timestamp(), end_dt.timestamp()


_REJECT_ACTIONS = {"curate_reject", "draft_reject"}


def stats_for_date(date_iso: str) -> dict:
    """Return today's counters.

    staging is cross-date (not dimensioned by date in Redis).
    rejected counts only entries with action in _REJECT_ACTIONS so future
    feedback actions (e.g., 'adjust', 'approve') do not inflate the metric.
    """
    client = _get_client()
    scraped = client.scard(f"platts:seen:{date_iso}")
    staging = sum(1 for _ in client.scan_iter(match="platts:staging:*", count=200))
    archived = sum(1 for _ in client.scan_iter(match=f"platts:archive:{date_iso}:*", count=200))
    pipeline = client.scard(f"platts:pipeline:processed:{date_iso}")
    start_ts, end_ts = _date_bounds_epoch(date_iso)
    # Filter by action: zrangebyscore returns members in the date window; HGET
    # each to check action. Volume is small (<100/day expected) so the extra
    # roundtrip is acceptable. Pipeline used to cap latency.
    members = client.zrangebyscore("webhook:feedback:index", start_ts, end_ts)
    rejected = 0
    if members:
        pipe = client.pipeline()
        for member in members:
            pipe.hget(_feedback_key(member), "action")
        actions = pipe.execute()
        rejected = sum(1 for a in actions if a in _REJECT_ACTIONS)
    return {
        "scraped": int(scraped),
        "staging": int(staging),
        "archived": int(archived),
        "rejected": int(rejected),
        "pipeline": int(pipeline),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_redis_queries.py -v`
Expected: 22 passing.

- [ ] **Step 5: Commit**

```bash
git add webhook/redis_queries.py tests/test_redis_queries.py
git commit -m "feat(queries): stats_for_date + mark_pipeline_processed (action-filtered)"
```

---

## Task 5: query_handlers — /help

**Files:**
- Create: `webhook/query_handlers.py`
- Create: `tests/test_query_handlers.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_query_handlers.py`:

```python
"""Tests for webhook.query_handlers formatters."""
import json
import time
import pytest
import fakeredis


@pytest.fixture
def fake_redis(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    from webhook import redis_queries
    monkeypatch.setattr(redis_queries, "_get_client", lambda: fake)
    monkeypatch.setattr(redis_queries, "_client", None)
    return fake


def test_help_text_lists_all_commands():
    from webhook.query_handlers import format_help
    text = format_help()
    assert "/queue" in text
    assert "/history" in text
    assert "/rejections" in text
    assert "/stats" in text
    assert "/status" in text
    assert "/reprocess" in text
    assert "/add" in text
    assert "/list" in text
    assert "/cancel" in text
    assert text.startswith("*COMANDOS*")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_query_handlers.py::test_help_text_lists_all_commands -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'webhook.query_handlers'`.

- [ ] **Step 3: Implement**

Create `webhook/query_handlers.py`:

```python
"""Bot navigation command formatters.

Each handler returns a plain string (Markdown-safe) for the webhook
layer to send via Telegram. Callback-producing handlers also return an
optional reply_markup dict.

The handlers here do not know about Flask, requests, or Telegram — they
consume webhook.redis_queries and produce text. app.py wires them to
the chat.
"""
from webhook import redis_queries


_HELP_TEXT = """*COMANDOS*

/queue — items aguardando
/history — arquivo (últimos 10)
/rejections — recusas (últimas 10)
/stats — contadores de hoje
/status — saúde do sistema
/reprocess <id> — re-dispara pipeline
/add, /list — contatos
/cancel — abortar fluxo"""


def format_help() -> str:
    """Return the /help text (static)."""
    return _HELP_TEXT
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_query_handlers.py -v`
Expected: 1 passing.

- [ ] **Step 5: Commit**

```bash
git add webhook/query_handlers.py tests/test_query_handlers.py
git commit -m "feat(queries): /help formatter"
```

---

## Task 6: /history handler

**Files:**
- Modify: `webhook/query_handlers.py`
- Modify: `tests/test_query_handlers.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_query_handlers.py`:

```python
def test_history_empty(fake_redis):
    from webhook.query_handlers import format_history
    text = format_history()
    assert text == "*ARQUIVADOS*\n\nNenhum item arquivado."


def test_history_formats_items(fake_redis):
    from webhook.query_handlers import format_history
    fake_redis.set("platts:archive:2026-04-14:a", json.dumps({
        "id": "a", "title": "Bonds Municipais Sustentam Aço no Q2",
        "archivedAt": "2026-04-14T10:00:00+00:00"
    }))
    fake_redis.set("platts:archive:2026-04-13:b", json.dumps({
        "id": "b", "title": "Greve Port Hedland",
        "archivedAt": "2026-04-13T08:00:00+00:00"
    }))
    text = format_history()
    assert "*ARQUIVADOS · 2 mais recentes*" in text
    assert "1. Bonds Municipais Sustentam Aço no Q2 — 14/abr" in text
    assert "2. Greve Port Hedland — 13/abr" in text


def test_history_truncates_long_title(fake_redis):
    from webhook.query_handlers import format_history
    long_title = "A" * 80
    fake_redis.set("platts:archive:2026-04-15:x", json.dumps({
        "id": "x", "title": long_title,
        "archivedAt": "2026-04-15T10:00:00+00:00"
    }))
    text = format_history()
    assert "A" * 60 + "…" in text
    assert "A" * 61 not in text


def test_history_escapes_markdown_specials_in_title(fake_redis):
    """Titles with *, _, [, ` must be escaped to avoid Telegram 400 errors."""
    from webhook.query_handlers import format_history, _escape_md
    fake_redis.set("platts:archive:2026-04-15:x", json.dumps({
        "id": "x", "title": "Vale_Q2 *bonds* [draft] `code`",
        "archivedAt": "2026-04-15T10:00:00+00:00",
    }))
    text = format_history()
    # Raw specials must NOT appear unescaped
    assert "*bonds*" not in text
    assert "Vale_Q2" not in text
    assert "[draft]" not in text
    assert "`code`" not in text
    # Escaped form must appear
    assert _escape_md("Vale_Q2 *bonds* [draft] `code`") in text


def test_escape_md_helper():
    from webhook.query_handlers import _escape_md
    assert _escape_md("a*b_c[d]`e") == r"a\*b\_c\[d\]\`e"
    assert _escape_md("") == ""
    assert _escape_md(None) == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_query_handlers.py::test_history_empty -v`
Expected: FAIL with `ImportError: cannot import name 'format_history'`.

- [ ] **Step 3: Implement**

Append to `webhook/query_handlers.py`:

```python
_MONTHS_PT = [
    "jan", "fev", "mar", "abr", "mai", "jun",
    "jul", "ago", "set", "out", "nov", "dez",
]

_MD_SPECIALS = ("\\", "*", "_", "[", "]", "`")


def _escape_md(text) -> str:
    """Escape Telegram Markdown (legacy) specials so dynamic content does
    not break parse_mode=Markdown sends.

    Order matters: backslash must be first so subsequent replacements do
    not double-escape inserted backslashes.
    """
    if text is None:
        return ""
    s = str(text)
    for ch in _MD_SPECIALS:
        s = s.replace(ch, "\\" + ch)
    return s


def _format_short_date(iso_date: str) -> str:
    """'2026-04-14' -> '14/abr'. Returns '' on parse failure."""
    if not iso_date or len(iso_date) < 10:
        return ""
    try:
        year, month, day = iso_date[:10].split("-")
        month_idx = int(month) - 1
        if not 0 <= month_idx < 12:
            return ""
        return f"{int(day):02d}/{_MONTHS_PT[month_idx]}"
    except (ValueError, IndexError):
        return ""


def _truncate(text: str, limit: int = 60) -> str:
    """Truncate to limit chars with trailing '…'."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def format_history(limit: int = 10) -> str:
    """Return /history text — last N archived items cross-date."""
    items = redis_queries.list_archive_recent(limit=limit)
    if not items:
        return "*ARQUIVADOS*\n\nNenhum item arquivado."
    lines = [f"*ARQUIVADOS · {len(items)} mais recentes*", ""]
    for i, item in enumerate(items, start=1):
        title = _escape_md(_truncate(item.get("title") or ""))
        date = _format_short_date(item.get("archived_date") or "")
        lines.append(f"{i}. {title} — {date}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_query_handlers.py -v`
Expected: 6 passing.

- [ ] **Step 5: Commit**

```bash
git add webhook/query_handlers.py tests/test_query_handlers.py
git commit -m "feat(queries): /history formatter with markdown escape"
```

---

## Task 7: /stats handler

**Files:**
- Modify: `webhook/query_handlers.py`
- Modify: `tests/test_query_handlers.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_query_handlers.py`:

```python
def test_stats_empty_day(fake_redis):
    from webhook.query_handlers import format_stats
    text = format_stats("2026-04-15")
    assert "*HOJE · 15/abr*" in text
    assert "Scraped     0" in text
    assert "Staging     0" in text
    assert "Arquivados  0" in text
    assert "Recusados   0" in text
    assert "Pipeline    0" in text


def test_stats_populated(fake_redis):
    from webhook.query_handlers import format_stats
    fake_redis.sadd("platts:seen:2026-04-15", "a", "b", "c", "d")
    fake_redis.set("platts:staging:s1", json.dumps({"id": "s1"}))
    fake_redis.set("platts:archive:2026-04-15:x1", json.dumps({"id": "x1"}))
    fake_redis.set("platts:archive:2026-04-15:x2", json.dumps({"id": "x2"}))
    fake_redis.sadd("platts:pipeline:processed:2026-04-15", "p1")
    text = format_stats("2026-04-15")
    assert "Scraped     4" in text
    assert "Staging     1" in text
    assert "Arquivados  2" in text
    assert "Pipeline    1" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_query_handlers.py::test_stats_empty_day -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement**

Append to `webhook/query_handlers.py`:

```python
def format_stats(date_iso: str) -> str:
    """Return /stats text for the given ISO date."""
    stats = redis_queries.stats_for_date(date_iso)
    short = _format_short_date(date_iso) or date_iso
    lines = [
        f"*HOJE · {short}*",
        "",
        f"Scraped     {stats['scraped']}",
        f"Staging     {stats['staging']}",
        f"Arquivados  {stats['archived']}",
        f"Recusados   {stats['rejected']}",
        f"Pipeline    {stats['pipeline']}",
    ]
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_query_handlers.py -v`
Expected: 8 passing.

- [ ] **Step 5: Commit**

```bash
git add webhook/query_handlers.py tests/test_query_handlers.py
git commit -m "feat(queries): /stats formatter"
```

---

## Task 8: /rejections handler

**Files:**
- Modify: `webhook/query_handlers.py`
- Modify: `tests/test_query_handlers.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_query_handlers.py`:

```python
def test_rejections_empty(fake_redis):
    from webhook.query_handlers import format_rejections
    text = format_rejections()
    assert text == "*RECUSAS*\n\nNenhuma recusa registrada."


def test_rejections_with_and_without_reason(fake_redis):
    from webhook.query_handlers import format_rejections
    from webhook.redis_queries import save_feedback
    save_feedback("curate_reject", "a", 1, "", "First")
    time.sleep(0.01)
    save_feedback("curate_reject", "b", 1, "duplicata", "Second")
    text = format_rejections()
    assert "*RECUSAS · últimas 2*" in text
    # newest first: b, then a
    assert '"duplicata"' in text
    assert "_(sem razão)_" in text


def test_rejections_truncates_long_reason(fake_redis):
    from webhook.query_handlers import format_rejections
    from webhook.redis_queries import save_feedback
    long = "x" * 120
    save_feedback("curate_reject", "a", 1, long, "T")
    text = format_rejections()
    assert "x" * 80 + "…" in text
    assert "x" * 81 not in text


def test_rejections_escapes_markdown_in_reason(fake_redis):
    """Reason can contain user free-text — escape * _ ` [ to avoid 400."""
    from webhook.query_handlers import format_rejections, _escape_md
    from webhook.redis_queries import save_feedback
    save_feedback("curate_reject", "a", 1, "duplicate of *foo* [bar]", "T")
    text = format_rejections()
    assert "*foo*" not in text
    assert "[bar]" not in text
    assert _escape_md("duplicate of *foo* [bar]") in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_query_handlers.py::test_rejections_empty -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement**

Append to `webhook/query_handlers.py`:

```python
from datetime import datetime, timezone


def _format_hhmm(epoch_seconds: float) -> str:
    """Epoch seconds -> 'HH:MM' UTC."""
    try:
        return datetime.fromtimestamp(float(epoch_seconds), tz=timezone.utc).strftime("%H:%M")
    except (ValueError, OSError):
        return "??:??"


def format_rejections(limit: int = 10) -> str:
    """Return /rejections text — last N feedback entries."""
    entries = redis_queries.list_feedback(limit=limit)
    if not entries:
        return "*RECUSAS*\n\nNenhuma recusa registrada."
    lines = [f"*RECUSAS · últimas {len(entries)}*", ""]
    for i, entry in enumerate(entries, start=1):
        when = _format_hhmm(entry.get("timestamp") or 0)
        reason = entry.get("reason") or ""
        if reason:
            reason_fmt = f'"{_escape_md(_truncate(reason, 80))}"'
        else:
            reason_fmt = "_(sem razão)_"
        lines.append(f"{i}. {when} · {reason_fmt}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_query_handlers.py -v`
Expected: 12 passing.

- [ ] **Step 5: Commit**

```bash
git add webhook/query_handlers.py tests/test_query_handlers.py
git commit -m "feat(queries): /rejections formatter with markdown escape"
```

---

## Task 9: /queue handler + pagination

**Files:**
- Modify: `webhook/query_handlers.py`
- Modify: `tests/test_query_handlers.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_query_handlers.py`:

```python
def test_queue_empty(fake_redis):
    from webhook.query_handlers import format_queue_page
    text, markup = format_queue_page(page=1)
    assert text == "*STAGING*\n\nNenhum item aguardando."
    assert markup is None


def test_queue_single_page(fake_redis):
    from webhook.query_handlers import format_queue_page
    for i, ts in enumerate(["10:00", "09:00", "08:00"]):
        fake_redis.set(f"platts:staging:item{i}", json.dumps({
            "id": f"item{i}", "title": f"Title {i}",
            "stagedAt": f"2026-04-15T{ts}:00Z"
        }))
    text, markup = format_queue_page(page=1)
    assert "*STAGING · 3 items*" in text
    assert "1. Title 0" in text
    assert "3. Title 2" in text
    # Single page -> no pagination row, only 3 item buttons
    buttons = markup["inline_keyboard"]
    # 3 item rows, no pagination
    assert len(buttons) == 3
    assert buttons[0][0]["callback_data"] == "queue_open:item0"


def test_queue_paginated(fake_redis):
    from webhook.query_handlers import format_queue_page
    # 12 items total -> 3 pages of 5
    for i in range(12):
        fake_redis.set(f"platts:staging:i{i:02d}", json.dumps({
            "id": f"i{i:02d}", "title": f"Title {i:02d}",
            "stagedAt": f"2026-04-15T{i:02d}:00:00Z"
        }))
    text_p1, markup_p1 = format_queue_page(page=1)
    assert "*STAGING · 12 items*" in text_p1
    assert "1. Title 11" in text_p1
    assert "5. Title 07" in text_p1
    assert "6. Title" not in text_p1
    # markup: 5 item rows + 1 pagination row
    assert len(markup_p1["inline_keyboard"]) == 6
    pag = markup_p1["inline_keyboard"][-1]
    pag_texts = [btn["text"] for btn in pag]
    # Page 1: no "anterior" (no previous page); indicator + próximo
    assert any("1/3" in t for t in pag_texts)
    assert any("próximo" in t.lower() for t in pag_texts)
    assert not any("anterior" in t.lower() for t in pag_texts)

    text_p2, markup_p2 = format_queue_page(page=2)
    assert "6. Title 06" in text_p2
    assert "10. Title 02" in text_p2


def test_queue_truncates_long_title(fake_redis):
    from webhook.query_handlers import format_queue_page
    long_title = "B" * 80
    fake_redis.set("platts:staging:x", json.dumps({
        "id": "x", "title": long_title, "stagedAt": "2026-04-15T10:00:00Z"
    }))
    text, _ = format_queue_page(page=1)
    assert "B" * 60 + "…" in text


def test_queue_escapes_markdown_in_title(fake_redis):
    from webhook.query_handlers import format_queue_page, _escape_md
    fake_redis.set("platts:staging:x", json.dumps({
        "id": "x", "title": "Vale_Q2 *report* [draft]",
        "stagedAt": "2026-04-15T10:00:00Z",
    }))
    text, _ = format_queue_page(page=1)
    assert "*report*" not in text
    assert "Vale_Q2" not in text
    assert _escape_md("Vale_Q2 *report* [draft]") in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_query_handlers.py::test_queue_empty -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement**

Append to `webhook/query_handlers.py`:

```python
_QUEUE_PAGE_SIZE = 5


def format_queue_page(page: int = 1) -> tuple[str, Optional[dict]]:
    """Return (text, reply_markup) for /queue at given 1-indexed page.

    reply_markup is None when there are no items. Each item row has a
    single callback button 'queue_open:<id>' that the dispatch layer
    maps to rendering the full curation card. Pagination row appended
    if total pages > 1.
    """
    items = redis_queries.list_staging(limit=200)
    total = len(items)
    if total == 0:
        return "*STAGING*\n\nNenhum item aguardando.", None

    total_pages = (total + _QUEUE_PAGE_SIZE - 1) // _QUEUE_PAGE_SIZE
    page = max(1, min(page, total_pages))
    start = (page - 1) * _QUEUE_PAGE_SIZE
    end = start + _QUEUE_PAGE_SIZE
    page_items = items[start:end]

    lines = [f"*STAGING · {total} items*", ""]
    for i, item in enumerate(page_items, start=start + 1):
        title = _escape_md(_truncate(item.get("title") or ""))
        lines.append(f"{i}. {title}")
    text = "\n".join(lines)

    keyboard: list[list[dict]] = []
    for i, item in enumerate(page_items, start=start + 1):
        item_id = item.get("id") or ""
        keyboard.append([{
            "text": f"{i}. Abrir",
            "callback_data": f"queue_open:{item_id}",
        }])

    if total_pages > 1:
        row: list[dict] = []
        if page > 1:
            row.append({"text": "⬅ anterior", "callback_data": f"queue_page:{page - 1}"})
        row.append({"text": f"{page}/{total_pages}", "callback_data": "noop"})
        if page < total_pages:
            row.append({"text": "próximo ➡", "callback_data": f"queue_page:{page + 1}"})
        keyboard.append(row)

    return text, {"inline_keyboard": keyboard}
```

Also add to the import block at the top of `webhook/query_handlers.py`:

```python
from typing import Optional
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_query_handlers.py -v`
Expected: 17 passing.

- [ ] **Step 5: Commit**

```bash
git add webhook/query_handlers.py tests/test_query_handlers.py
git commit -m "feat(queries): /queue with pagination, callbacks, markdown escape"
```

---

## Task 10: Wire /help /history /stats /rejections dispatches in app.py

**Files:**
- Modify: `webhook/app.py` (handle_message function — add 4 command branches)

- [ ] **Step 1: Locate the dispatch block**

Open `webhook/app.py` and find the section where other slash-commands are dispatched (around `/status`, `/add`, `/list` handling in `handle_message`). The commands are dispatched as `if text.startswith("/status"): ...` elif blocks.

Search for:
```python
grep -n '/status\|/add\|/list' webhook/app.py
```

- [ ] **Step 2: Add imports at the top of the file**

Near the top of `webhook/app.py`, next to the existing imports, add:

```python
from webhook import query_handlers, redis_queries
```

- [ ] **Step 3: Add dispatch branches**

Inside `handle_message`, where the existing command dispatch lives (after `/status` / `/list` / `/add` handling, before the "no command matched" fallthrough), add:

```python
elif text == "/help":
    if not contact_admin.is_authorized(chat_id):
        logger.warning(f"/help rejected: chat_id={chat_id} not authorized")
        return jsonify({"ok": True})
    send_telegram_message(chat_id, query_handlers.format_help())
    return jsonify({"ok": True})

elif text == "/history":
    if not contact_admin.is_authorized(chat_id):
        logger.warning(f"/history rejected: chat_id={chat_id} not authorized")
        return jsonify({"ok": True})
    try:
        body = query_handlers.format_history()
    except Exception as exc:
        logger.error(f"/history error: {exc}")
        send_telegram_message(chat_id, "❌ Erro ao consultar arquivo.")
        return jsonify({"ok": True})
    send_telegram_message(chat_id, body)
    return jsonify({"ok": True})

elif text == "/stats":
    if not contact_admin.is_authorized(chat_id):
        logger.warning(f"/stats rejected: chat_id={chat_id} not authorized")
        return jsonify({"ok": True})
    today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        body = query_handlers.format_stats(today_iso)
    except Exception as exc:
        logger.error(f"/stats error: {exc}")
        send_telegram_message(chat_id, "❌ Erro ao calcular stats.")
        return jsonify({"ok": True})
    send_telegram_message(chat_id, body)
    return jsonify({"ok": True})

elif text == "/rejections":
    if not contact_admin.is_authorized(chat_id):
        logger.warning(f"/rejections rejected: chat_id={chat_id} not authorized")
        return jsonify({"ok": True})
    try:
        body = query_handlers.format_rejections()
    except Exception as exc:
        logger.error(f"/rejections error: {exc}")
        send_telegram_message(chat_id, "❌ Erro ao listar recusas.")
        return jsonify({"ok": True})
    send_telegram_message(chat_id, body)
    return jsonify({"ok": True})
```

- [ ] **Step 4: Syntax check**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python3 -c "import ast; ast.parse(open('webhook/app.py').read()); print('syntax ok')"`
Expected: `syntax ok`.

- [ ] **Step 5: Commit**

```bash
git add webhook/app.py
git commit -m "feat(app): dispatch /help /history /stats /rejections"
```

---

## Task 11: Wire /queue dispatch + queue callbacks in app.py

**Files:**
- Modify: `webhook/app.py` (handle_message + handle_callback)

- [ ] **Step 1: Add /queue command dispatch**

Inside `handle_message`, in the same region as Task 10, add:

```python
elif text == "/queue":
    if not contact_admin.is_authorized(chat_id):
        logger.warning(f"/queue rejected: chat_id={chat_id} not authorized")
        return jsonify({"ok": True})
    try:
        body, markup = query_handlers.format_queue_page(page=1)
    except Exception as exc:
        logger.error(f"/queue error: {exc}")
        send_telegram_message(chat_id, "❌ Erro ao consultar staging.")
        return jsonify({"ok": True})
    send_telegram_message(chat_id, body, reply_markup=markup)
    return jsonify({"ok": True})
```

- [ ] **Step 2: Add callbacks for queue_page and queue_open**

In `handle_callback` (find via `grep -n 'def handle_callback' webhook/app.py`), find the existing `if callback_data.startswith("pg:"):` block (used by contact /list pagination) and add siblings for `queue_page` and `queue_open`:

```python
if callback_data.startswith("queue_page:"):
    if not contact_admin.is_authorized(chat_id):
        answer_callback(callback_id, "Não autorizado")
        return jsonify({"ok": True})
    try:
        page = int(callback_data.split(":", 1)[1])
    except ValueError:
        answer_callback(callback_id, "Página inválida")
        return jsonify({"ok": True})
    answer_callback(callback_id, "")
    message_id = callback_query["message"]["message_id"]
    try:
        body, markup = query_handlers.format_queue_page(page=page)
    except Exception as exc:
        logger.error(f"queue_page error: {exc}")
        return jsonify({"ok": True})
    edit_message(chat_id, message_id, body, reply_markup=markup)
    return jsonify({"ok": True})

if callback_data.startswith("queue_open:"):
    if not contact_admin.is_authorized(chat_id):
        answer_callback(callback_id, "Não autorizado")
        return jsonify({"ok": True})
    item_id = callback_data.split(":", 1)[1]
    from execution.curation import redis_client as curation_redis
    from execution.curation import telegram_poster
    try:
        item = curation_redis.get_staging(item_id)
    except Exception as exc:
        logger.error(f"queue_open redis error: {exc}")
        answer_callback(callback_id, "⚠️ Redis indisponível")
        return jsonify({"ok": True})
    if item is None:
        answer_callback(callback_id, "⚠️ Item expirou")
        return jsonify({"ok": True})
    answer_callback(callback_id, "")
    preview_base_url = os.getenv("TELEGRAM_WEBHOOK_URL", "").rstrip("/")
    try:
        telegram_poster.post_for_curation(chat_id, item, preview_base_url)
    except Exception as exc:
        logger.error(f"queue_open post error: {exc}")
        send_telegram_message(chat_id, "❌ Erro ao abrir card.")
    return jsonify({"ok": True})
```

Place these blocks BEFORE the `parts = callback_data.split(":", 1)` line that parses other callbacks.

- [ ] **Step 3: Syntax check**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python3 -c "import ast; ast.parse(open('webhook/app.py').read()); print('syntax ok')"`
Expected: `syntax ok`.

- [ ] **Step 4: Commit**

```bash
git add webhook/app.py
git commit -m "feat(app): /queue dispatch + queue_page and queue_open callbacks"
```

---

## Task 12: Wire mark_pipeline_processed in curate_pipeline handler

**Files:**
- Modify: `webhook/app.py` (curate_pipeline branch in handle_callback)

- [ ] **Step 1: Locate the curate_pipeline branch**

In `handle_callback`, find the `elif action == "curate_pipeline":` branch (around line 1536 in the current file).

Run: `grep -n 'curate_pipeline' webhook/app.py`

- [ ] **Step 2: Add mark_pipeline_processed after successful staging-read**

In `curate_pipeline`, right after the successful `item = redis_client.get_staging(item_id)` and before the `_run_pipeline_and_archive` thread starts, add:

```python
try:
    redis_queries.mark_pipeline_processed(item_id, datetime.now(timezone.utc).strftime("%Y-%m-%d"))
except Exception as exc:
    logger.warning(f"mark_pipeline_processed failed for {item_id}: {exc}")
```

The `redis_queries` import was already added in Task 10.

- [ ] **Step 3: Syntax check**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python3 -c "import ast; ast.parse(open('webhook/app.py').read()); print('syntax ok')"`
Expected: `syntax ok`.

- [ ] **Step 4: Commit**

```bash
git add webhook/app.py
git commit -m "feat(app): track pipeline clicks in platts:pipeline:processed"
```

---

## Task 13: REJECT_REASON_STATE + capture flow

**Files:**
- Modify: `webhook/app.py`
- Create: `tests/test_reject_reason_flow.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_reject_reason_flow.py`:

```python
"""Tests for the rejection-reason capture flow.

Uses fakeredis + direct calls to the helper functions in app.py. The
Flask request layer is tested in integration tests elsewhere; here we
verify the state machine in isolation.
"""
import time
import pytest
import fakeredis


@pytest.fixture
def fake_redis(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    from webhook import redis_queries
    monkeypatch.setattr(redis_queries, "_get_client", lambda: fake)
    monkeypatch.setattr(redis_queries, "_client", None)
    return fake


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    from webhook import app as webhook_app
    webhook_app.REJECT_REASON_STATE.clear()
    webhook_app.ADJUST_STATE.clear()
    yield
    webhook_app.REJECT_REASON_STATE.clear()
    webhook_app.ADJUST_STATE.clear()


def test_begin_reject_reason_stores_state_and_saves_feedback(fake_redis):
    from webhook.app import begin_reject_reason, REJECT_REASON_STATE
    from webhook.redis_queries import list_feedback
    key = begin_reject_reason(
        chat_id=999, action="curate_reject",
        item_id="abc123", title="Sample title",
    )
    assert key is not None
    assert 999 in REJECT_REASON_STATE
    assert REJECT_REASON_STATE[999]["feedback_key"] == key
    entries = list_feedback(limit=10)
    assert len(entries) == 1
    assert entries[0]["reason"] == ""
    assert entries[0]["item_id"] == "abc123"
    assert entries[0]["title"] == "Sample title"


def test_consume_reject_reason_with_text(fake_redis):
    from webhook.app import begin_reject_reason, consume_reject_reason, REJECT_REASON_STATE
    from webhook.redis_queries import list_feedback
    begin_reject_reason(chat_id=999, action="curate_reject", item_id="x", title="T")
    consumed = consume_reject_reason(chat_id=999, text="não é iron ore")
    assert consumed == ("saved", "não é iron ore")
    assert 999 not in REJECT_REASON_STATE
    entries = list_feedback(limit=10)
    assert entries[0]["reason"] == "não é iron ore"


def test_consume_reject_reason_skip_pt(fake_redis):
    from webhook.app import begin_reject_reason, consume_reject_reason
    begin_reject_reason(chat_id=999, action="curate_reject", item_id="x", title="T")
    consumed = consume_reject_reason(chat_id=999, text="pular")
    assert consumed == ("skipped", "")


def test_consume_reject_reason_skip_en(fake_redis):
    from webhook.app import begin_reject_reason, consume_reject_reason
    begin_reject_reason(chat_id=999, action="curate_reject", item_id="x", title="T")
    consumed = consume_reject_reason(chat_id=999, text="SKIP")
    assert consumed == ("skipped", "")


def test_consume_reject_reason_no_state_returns_none(fake_redis):
    from webhook.app import consume_reject_reason
    consumed = consume_reject_reason(chat_id=999, text="random text")
    assert consumed is None


def test_consume_reject_reason_expired_state_returns_none(fake_redis, monkeypatch):
    from webhook import app as webhook_app
    webhook_app.begin_reject_reason(chat_id=999, action="curate_reject", item_id="x", title="T")
    # Force expiration
    webhook_app.REJECT_REASON_STATE[999]["expires_at"] = time.time() - 10
    consumed = webhook_app.consume_reject_reason(chat_id=999, text="too late")
    assert consumed is None
    assert 999 not in webhook_app.REJECT_REASON_STATE


def test_adjust_state_takes_precedence_in_handle_message(fake_redis, monkeypatch):
    """End-to-end: with BOTH states set, the adjust handler consumes the
    message and the reject feedback reason remains empty.

    Drives the real Flask handler via test_client so the cascade order in
    telegram_webhook is exercised, not just asserted by inspection. Relies
    on the synchronous `del ADJUST_STATE[chat_id]` happening BEFORE the
    daemon thread starts — no thread join needed.
    """
    from webhook import app as webhook_app
    from webhook.redis_queries import list_feedback

    # Stub the heavy AI processor; we only care about the cascade decision
    monkeypatch.setattr(webhook_app, "process_adjustment_async",
                        lambda *args, **kwargs: None)

    webhook_app.ADJUST_STATE[999] = {"draft_id": "d1", "awaiting_feedback": True}
    webhook_app.begin_reject_reason(chat_id=999, action="curate_reject",
                                    item_id="x", title="T")

    client = webhook_app.app.test_client()
    payload = {"message": {"chat": {"id": 999},
                           "text": "this should go to ADJUST not REJECT"}}
    resp = client.post("/webhook", json=payload)
    assert resp.status_code == 200

    # ADJUST consumed the message (cleared its state synchronously in the dispatch)
    assert 999 not in webhook_app.ADJUST_STATE
    # REJECT state still present — was NOT consumed
    assert 999 in webhook_app.REJECT_REASON_STATE
    # The placeholder feedback's reason was NOT overwritten with the message text
    entries = list_feedback(limit=10)
    assert entries, "begin_reject_reason should have left a placeholder entry"
    assert entries[0]["reason"] == ""
```

> **Note on Step 4 wiring:** when adding the cascade in `telegram_webhook`, ensure the ADJUST check runs BEFORE `consume_reject_reason`. The test above will fail loudly if that ordering is reversed.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_reject_reason_flow.py::test_begin_reject_reason_stores_state_and_saves_feedback -v`
Expected: FAIL with `ImportError: cannot import name 'begin_reject_reason'`.

- [ ] **Step 3: Add state dict and helpers at top of app.py**

Near the top of `webhook/app.py` (in the section where `ADJUST_STATE = {}` lives), add:

```python
REJECT_REASON_STATE: dict = {}
REJECT_REASON_TIMEOUT_SECONDS = 120


def begin_reject_reason(chat_id: int, action: str, item_id: str, title: str) -> str:
    """Save a placeholder feedback entry and set the state to await a reason message.

    Returns the feedback_key so callers can display it if useful.
    """
    import time
    feedback_key = redis_queries.save_feedback(
        action=action, item_id=item_id, chat_id=chat_id, reason="", title=title or "",
    )
    REJECT_REASON_STATE[chat_id] = {
        "feedback_key": feedback_key,
        "expires_at": time.time() + REJECT_REASON_TIMEOUT_SECONDS,
    }
    return feedback_key


def consume_reject_reason(chat_id: int, text: str):
    """Consume the next user message as the rejection reason.

    Returns:
        ('saved', reason_text)  if a reason was saved
        ('skipped', '')         if the user typed 'pular' or 'skip'
        None                    if there is no pending state or it expired
    """
    import time
    state = REJECT_REASON_STATE.get(chat_id)
    if state is None:
        return None
    if time.time() >= state.get("expires_at", 0):
        REJECT_REASON_STATE.pop(chat_id, None)
        return None
    feedback_key = state["feedback_key"]
    stripped = (text or "").strip()
    if stripped.lower() in {"pular", "skip"}:
        REJECT_REASON_STATE.pop(chat_id, None)
        return ("skipped", "")
    redis_queries.update_feedback_reason(feedback_key, stripped)
    REJECT_REASON_STATE.pop(chat_id, None)
    return ("saved", stripped)
```

- [ ] **Step 4: Wire consume_reject_reason into handle_message cascade**

In `handle_message`, find the block that checks `ADJUST_STATE` (Task: `# ── Check if user is in adjustment mode ──`). Right AFTER that block (not before — adjust wins precedence), add:

```python
# ── Check if user is responding to a rejection-reason prompt ──
reject_result = consume_reject_reason(chat_id, text)
if reject_result is not None:
    status, reason = reject_result
    if status == "saved":
        send_telegram_message(chat_id, "✅ Razão registrada.")
    else:
        send_telegram_message(chat_id, "✅ Ok, sem razão registrada.")
    return jsonify({"ok": True})
```

- [ ] **Step 5: Modify curate_reject to begin reason capture**

Find `elif action == "curate_reject":` in `handle_callback`. Replace its body with:

```python
elif action == "curate_reject":
    if not contact_admin.is_authorized(chat_id):
        answer_callback(callback_id, "Não autorizado")
        return jsonify({"ok": True})
    item_id = parts[1] if len(parts) > 1 else ""
    from execution.curation import redis_client
    # Snapshot title before discard
    snapshot_title = ""
    try:
        item = redis_client.get_staging(item_id)
        if item:
            snapshot_title = item.get("title") or ""
    except Exception:
        pass
    try:
        redis_client.discard(item_id)
    except Exception as exc:
        logger.error(f"curate_reject redis error: {exc}")
        answer_callback(callback_id, "⚠️ Redis indisponível")
        return jsonify({"ok": True})
    try:
        begin_reject_reason(chat_id, "curate_reject", item_id, snapshot_title)
    except Exception as exc:
        logger.error(f"curate_reject begin_reject_reason error: {exc}")
    answer_callback(callback_id, "❌ Recusado")
    finalize_card(
        chat_id,
        callback_query,
        f"❌ *Recusado* em {datetime.now(timezone.utc).strftime('%H:%M')} UTC\n"
        f"🆔 `{item_id}`\n\n"
        f"Por quê? (opcional, responda ou `pular`)",
    )
    return jsonify({"ok": True})
```

- [ ] **Step 6: Modify draft reject handler to begin reason capture**

Find `elif action == "reject":` in `handle_callback` (the draft-reject one, earlier in the function than curate_reject). Replace with:

```python
elif action == "reject":
    # Snapshot title before update
    snapshot_title = ""
    draft = drafts_get(draft_id)
    if draft:
        # Take the first non-empty bold line from the draft message as title
        msg = draft.get("message") or ""
        for line in msg.splitlines():
            stripped = line.strip().lstrip("📊").strip()
            if stripped and stripped != "*MINERALS TRADING*":
                snapshot_title = stripped[:80]
                break
        if not snapshot_title:
            snapshot_title = f"Draft {draft_id[:8]}"
    else:
        snapshot_title = f"Draft {draft_id[:8]}"

    if drafts_contains(draft_id):
        drafts_update(draft_id, status="rejected")
    try:
        begin_reject_reason(chat_id, "draft_reject", draft_id, snapshot_title)
    except Exception as exc:
        logger.error(f"draft reject begin_reject_reason error: {exc}")
    answer_callback(callback_id, "❌ Rejeitado")
    finalize_card(
        chat_id,
        callback_query,
        f"❌ *Recusado* em {datetime.now(timezone.utc).strftime('%H:%M')} UTC\n\n"
        f"Por quê? (opcional, responda ou `pular`)",
    )
    return jsonify({"ok": True})
```

- [ ] **Step 7: Run all tests**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_reject_reason_flow.py -v`
Expected: 7 passing.

Then run the full suite to verify no regression:
Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest -v`
Expected: all previously-passing tests still pass.

- [ ] **Step 8: Commit**

```bash
git add webhook/app.py tests/test_reject_reason_flow.py
git commit -m "feat(app): reject-reason capture state + flow

Both curate_reject (item card) and draft reject save an initial
feedback entry with empty reason, prompt the user for an optional
explanation, and update the feedback hash on the next message.

State lives in REJECT_REASON_STATE with a 120s timeout so late
messages fall through to normal handling."
```

---

## Task 14: setMyCommands registration route

**Files:**
- Modify: `webhook/app.py`

- [ ] **Step 1: Add the registration route**

At the end of `webhook/app.py`, before the `if __name__ == "__main__"` block (or alongside other `@app.route` definitions), add:

```python
@app.route("/admin/register-commands", methods=["POST"])
def register_commands():
    """Register bot commands with Telegram's setMyCommands so they appear
    in the / autocomplete menu. Call this manually (e.g. via curl) once
    after deploy or whenever the command list changes.

    Auth: requires a chat_id query param that belongs to an authorized admin.
    """
    raw_chat_id = request.args.get("chat_id", "")
    try:
        chat_id = int(raw_chat_id)
    except ValueError:
        return jsonify({"ok": False, "error": "chat_id query param required"}), 400
    if not contact_admin.is_authorized(chat_id):
        return jsonify({"ok": False, "error": "unauthorized"}), 403

    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return jsonify({"ok": False, "error": "TELEGRAM_BOT_TOKEN missing"}), 500

    commands = [
        {"command": "help", "description": "Lista todos os comandos"},
        {"command": "queue", "description": "Items aguardando curadoria"},
        {"command": "history", "description": "Ultimos 10 arquivados"},
        {"command": "rejections", "description": "Ultimas 10 recusas"},
        {"command": "stats", "description": "Contadores de hoje"},
        {"command": "status", "description": "Saude dos workflows"},
        {"command": "reprocess", "description": "Re-dispara pipeline num item"},
        {"command": "add", "description": "Adicionar contato"},
        {"command": "list", "description": "Listar contatos"},
        {"command": "cancel", "description": "Abortar fluxo atual"},
    ]
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/setMyCommands",
            json={"commands": commands},
            timeout=10,
        )
        data = resp.json()
    except Exception as exc:
        logger.error(f"setMyCommands request failed: {exc}")
        return jsonify({"ok": False, "error": str(exc)}), 502
    if not data.get("ok"):
        logger.error(f"setMyCommands returned not-ok: {data}")
        return jsonify({"ok": False, "telegram": data}), 502
    logger.info(f"setMyCommands registered {len(commands)} commands")
    return jsonify({"ok": True, "registered": len(commands)})
```

- [ ] **Step 2: Syntax check**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python3 -c "import ast; ast.parse(open('webhook/app.py').read()); print('syntax ok')"`
Expected: `syntax ok`.

- [ ] **Step 3: Run the full test suite**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest -v`
Expected: all tests pass (including ~33 new across queries + handlers + reject flow).

- [ ] **Step 4: Commit**

```bash
git add webhook/app.py
git commit -m "feat(app): POST /admin/register-commands for setMyCommands"
```

---

## Task 15: Manual validation in production

**Files:** (none — pure validation)

- [ ] **Step 1: Push to main**

```bash
cd "/Users/bigode/Dev/Antigravity WF "
git push origin main
```

Wait for Railway to redeploy (~2 min).

- [ ] **Step 2: Register commands**

From your authorized admin chat_id, call:

```bash
curl -X POST "https://<railway-url>/admin/register-commands?chat_id=<YOUR_CHAT_ID>"
```

Expected response: `{"ok": true, "registered": 10}`.

- [ ] **Step 3: Verify autocomplete in Telegram**

In the bot chat, type `/` — the 10 registered commands should appear as autocomplete suggestions with descriptions.

- [ ] **Step 4: Smoke each new command**

- `/help` — receive the command list text
- `/queue` — if staging is non-empty, see numbered list with pagination buttons; click a row → full curation card appears
- `/history` — see up to 10 archived items
- `/stats` — see today's counters
- `/rejections` — on first run likely empty; after recusing an item next time, re-run and confirm it shows up
- Click "❌ Recusar" on any card → card changes to "Recusado em HH:MM UTC / Por quê? (opcional...)" → reply "teste de razão" → receive "✅ Razão registrada" → re-run `/rejections` and confirm the reason appears
- Click "❌ Recusar" on another card → reply `pular` → receive "✅ Ok, sem razão registrada" → `/rejections` shows `_(sem razão)_`

- [ ] **Step 5: Mark milestone complete**

If all the above pass, the milestone is done. If any step fails, capture the log and iterate with targeted fixes.
