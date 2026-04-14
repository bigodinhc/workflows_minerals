# Platts Curation via Telegram — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Substituir os dois scripts redundantes (`rationale_ingestion.py`, `market_news_ingestion.py`) por um script único que roteia artigos do Platts: rationale auto-processado via `RationaleAgent`, outros itens vão pra Telegram com preview + 4 botões (arquivar/recusar/3-agents/preview HTML).

**Architecture:** Unified ingestion → dedup via Redis SET → classification router → dispatcha rationale pro `RationaleAgent` (1x/dia, gated) OU grava Redis staging + posta Telegram pra curadoria. Novos callbacks no webhook Flask + nova rota `/preview/<id>`.

**Tech Stack:** Python 3.9+ (pytest + fakeredis), Flask (Jinja), Redis (JSON strings + Set), Apify Python client, Telegram Bot API.

---

## File Structure

**New files:**
- `execution/curation/__init__.py` — marker
- `execution/curation/id_gen.py` — deterministic SHA256 id generator
- `execution/curation/redis_client.py` — wrapper sobre `REDIS_URL` com métodos de staging/archive/seen/flag
- `execution/curation/telegram_poster.py` — formata mensagem Markdown + inline keyboard, envia via `TelegramClient`
- `execution/curation/rationale_dispatcher.py` — wrapper pro fluxo rationale (aggregate → RationaleAgent → save draft → POST /store-draft → send approval)
- `execution/curation/router.py` — classifica itens (rationale vs curation) e dispara
- `execution/scripts/platts_ingestion.py` — entry point unificado
- `webhook/templates/preview.html` — Jinja template com Tailwind CDN
- `tests/test_curation_id_gen.py`
- `tests/test_curation_redis_client.py`
- `tests/test_curation_telegram_poster.py`
- `tests/test_curation_router.py`

**Modified:**
- `webhook/app.py` — adiciona rota `/preview/<id>` + 3 handlers (`curate_archive`, `curate_reject`, `curate_pipeline`) em `handle_callback()`

**Deleted:**
- `execution/agents/market_news_agent.py`
- `execution/scripts/market_news_ingestion.py`
- `execution/scripts/rationale_ingestion.py`

---

## Phase 1: Foundations (curation module)

### Task 1: id_gen — deterministic SHA256 id

**Files:**
- Create: `execution/curation/__init__.py`
- Create: `execution/curation/id_gen.py`
- Create: `tests/test_curation_id_gen.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_curation_id_gen.py`:

```python
"""Tests for execution.curation.id_gen."""


def test_generate_id_deterministic():
    from execution.curation.id_gen import generate_id
    a = generate_id("Top News - Ferrous Metals", "China steel output lags 2025")
    b = generate_id("Top News - Ferrous Metals", "China steel output lags 2025")
    assert a == b


def test_generate_id_different_for_different_input():
    from execution.curation.id_gen import generate_id
    a = generate_id("Top News - Ferrous Metals", "Title A")
    b = generate_id("Top News - Ferrous Metals", "Title B")
    assert a != b


def test_generate_id_length_is_12():
    from execution.curation.id_gen import generate_id
    result = generate_id("source", "title")
    assert len(result) == 12
    assert all(c in "0123456789abcdef" for c in result)


def test_generate_id_handles_empty_strings():
    from execution.curation.id_gen import generate_id
    result = generate_id("", "")
    assert len(result) == 12
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_curation_id_gen.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'execution.curation'`

- [ ] **Step 3: Create package marker + implementation**

Create `execution/curation/__init__.py` (empty file):

```python
```

Create `execution/curation/id_gen.py`:

```python
"""Deterministic ID generation for Platts items.

sha256(source + "::" + title) truncated to 12 hex chars. Stable cross-run,
enabling dedup via Redis SET and matching between staging/archive keys.
"""
import hashlib


def generate_id(source: str, title: str) -> str:
    """Generate a 12-char hex ID from source + title."""
    digest = hashlib.sha256(f"{source}::{title}".encode("utf-8")).hexdigest()
    return digest[:12]
```

- [ ] **Step 4: Run test, verify pass**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_curation_id_gen.py -v`
Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/curation/__init__.py execution/curation/id_gen.py tests/test_curation_id_gen.py && git commit -m "feat(curation): deterministic SHA256 id generator"
```

---

### Task 2: redis_client — Redis wrapper for curation keyspaces

**Files:**
- Create: `execution/curation/redis_client.py`
- Create: `tests/test_curation_redis_client.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_curation_redis_client.py`:

```python
"""Tests for execution.curation.redis_client."""
import json
import pytest
import fakeredis


@pytest.fixture
def fake_redis(monkeypatch):
    """Inject fakeredis as the module-level Redis client."""
    fake = fakeredis.FakeRedis(decode_responses=True)
    from execution.curation import redis_client
    monkeypatch.setattr(redis_client, "_get_client", lambda: fake)
    return fake


def test_set_and_get_staging_roundtrip(fake_redis):
    from execution.curation.redis_client import set_staging, get_staging
    item = {"id": "abc123", "title": "Test", "fullText": "body"}
    set_staging("abc123", item)
    got = get_staging("abc123")
    assert got == item


def test_set_staging_applies_48h_ttl(fake_redis):
    from execution.curation.redis_client import set_staging
    set_staging("abc123", {"id": "abc123"})
    ttl = fake_redis.ttl("platts:staging:abc123")
    # 48h = 172800s; allow some slack
    assert 172700 <= ttl <= 172800


def test_get_staging_returns_none_for_missing(fake_redis):
    from execution.curation.redis_client import get_staging
    assert get_staging("missing") is None


def test_archive_moves_from_staging_to_archive(fake_redis):
    from execution.curation.redis_client import set_staging, archive
    item = {"id": "abc123", "title": "Test"}
    set_staging("abc123", item)
    archived = archive("abc123", "2026-04-14", chat_id=12345)
    # Staging gone, archive present
    assert fake_redis.get("platts:staging:abc123") is None
    raw = fake_redis.get("platts:archive:2026-04-14:abc123")
    data = json.loads(raw)
    assert data["title"] == "Test"
    assert data["archivedBy"] == 12345
    assert "archivedAt" in data
    assert archived == data


def test_archive_returns_none_if_staging_missing(fake_redis):
    from execution.curation.redis_client import archive
    result = archive("missing", "2026-04-14", chat_id=1)
    assert result is None


def test_discard_deletes_staging(fake_redis):
    from execution.curation.redis_client import set_staging, discard
    set_staging("abc123", {"id": "abc123"})
    discard("abc123")
    assert fake_redis.get("platts:staging:abc123") is None


def test_seen_set_membership(fake_redis):
    from execution.curation.redis_client import is_seen, mark_seen
    assert is_seen("2026-04-14", "abc123") is False
    mark_seen("2026-04-14", "abc123")
    assert is_seen("2026-04-14", "abc123") is True


def test_mark_seen_applies_30d_ttl(fake_redis):
    from execution.curation.redis_client import mark_seen
    mark_seen("2026-04-14", "abc123")
    ttl = fake_redis.ttl("platts:seen:2026-04-14")
    # 30d = 2592000s
    assert 2591000 <= ttl <= 2592000


def test_rationale_flag_set_once_per_day(fake_redis):
    from execution.curation.redis_client import (
        is_rationale_processed,
        set_rationale_processed,
    )
    assert is_rationale_processed("2026-04-14") is False
    assert set_rationale_processed("2026-04-14") is True  # first time — NX wins
    assert is_rationale_processed("2026-04-14") is True
    assert set_rationale_processed("2026-04-14") is False  # second time — NX loses
```

- [ ] **Step 2: Run test, verify fail**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_curation_redis_client.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

Create `execution/curation/redis_client.py`:

```python
"""Redis keyspaces for Platts curation.

Keyspaces:
- platts:staging:<id>               JSON string, TTL 48h
- platts:archive:<date>:<id>        JSON string, no TTL (consumed by other project)
- platts:seen:<date>                Set of ids, TTL 30d (dedup)
- platts:rationale:processed:<date> String flag, TTL 30h (1x/day gate)

All functions are non-raising. Errors are logged by the caller via WorkflowLogger.
"""
import json
import os
from datetime import datetime, timezone
from typing import Optional

_STAGING_TTL_SECONDS = 48 * 60 * 60           # 48h
_SEEN_TTL_SECONDS = 30 * 24 * 60 * 60         # 30d
_RATIONALE_FLAG_TTL_SECONDS = 30 * 60 * 60    # 30h


def _get_client():
    """Return a Redis client using REDIS_URL env var. Raises if unset/unreachable."""
    import redis
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        raise RuntimeError("REDIS_URL env var not set")
    return redis.Redis.from_url(url, decode_responses=True)


def _staging_key(item_id: str) -> str:
    return f"platts:staging:{item_id}"


def _archive_key(date: str, item_id: str) -> str:
    return f"platts:archive:{date}:{item_id}"


def _seen_key(date: str) -> str:
    return f"platts:seen:{date}"


def _rationale_flag_key(date: str) -> str:
    return f"platts:rationale:processed:{date}"


def set_staging(item_id: str, item: dict) -> None:
    """Persist item as JSON with 48h TTL."""
    client = _get_client()
    client.set(_staging_key(item_id), json.dumps(item, ensure_ascii=False), ex=_STAGING_TTL_SECONDS)


def get_staging(item_id: str) -> Optional[dict]:
    """Return item JSON or None if missing/expired."""
    client = _get_client()
    raw = client.get(_staging_key(item_id))
    if raw is None:
        return None
    return json.loads(raw)


def archive(item_id: str, date: str, chat_id: int) -> Optional[dict]:
    """Move item from staging to archive. Returns archived dict or None if staging missing."""
    item = get_staging(item_id)
    if item is None:
        return None
    item = dict(item)
    item["archivedAt"] = datetime.now(timezone.utc).isoformat()
    item["archivedBy"] = chat_id
    client = _get_client()
    client.set(_archive_key(date, item_id), json.dumps(item, ensure_ascii=False))
    client.delete(_staging_key(item_id))
    return item


def discard(item_id: str) -> None:
    """Delete staging without archiving."""
    client = _get_client()
    client.delete(_staging_key(item_id))


def is_seen(date: str, item_id: str) -> bool:
    """Check if item id is in dedup set for date."""
    client = _get_client()
    return bool(client.sismember(_seen_key(date), item_id))


def mark_seen(date: str, item_id: str) -> None:
    """Add id to dedup set with 30d TTL refresh."""
    client = _get_client()
    client.sadd(_seen_key(date), item_id)
    client.expire(_seen_key(date), _SEEN_TTL_SECONDS)


def is_rationale_processed(date: str) -> bool:
    """Check if rationale pipeline already ran for date."""
    client = _get_client()
    return client.get(_rationale_flag_key(date)) is not None


def set_rationale_processed(date: str) -> bool:
    """SET NX + EXPIRE — returns True if we set it (first time), False if already set."""
    client = _get_client()
    result = client.set(_rationale_flag_key(date), "1", nx=True, ex=_RATIONALE_FLAG_TTL_SECONDS)
    return bool(result)
```

- [ ] **Step 4: Run test, verify pass**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_curation_redis_client.py -v`
Expected: 9 tests PASS

- [ ] **Step 5: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/curation/redis_client.py tests/test_curation_redis_client.py && git commit -m "feat(curation): redis client wrapper with staging/archive/seen/flag keyspaces"
```

---

### Task 3: telegram_poster — format & send curation messages

**Files:**
- Create: `execution/curation/telegram_poster.py`
- Create: `tests/test_curation_telegram_poster.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_curation_telegram_poster.py`:

```python
"""Tests for execution.curation.telegram_poster."""
from unittest.mock import MagicMock


def test_build_preview_truncates_long_text():
    from execution.curation.telegram_poster import build_preview
    body = "x" * 1000
    preview = build_preview(body, limit=400)
    assert preview.endswith("...")
    assert len(preview) <= 403


def test_build_preview_keeps_short_text():
    from execution.curation.telegram_poster import build_preview
    preview = build_preview("short text", limit=400)
    assert preview == "short text"


def test_format_message_for_top_news():
    from execution.curation.telegram_poster import format_message
    item = {
        "id": "abc123",
        "title": "China steel output lags 2025",
        "fullText": "China's steel production continues to trail year-ago levels...",
        "publishDate": "04/09/2026 13:46 UTC",
        "source": "Top News - Ferrous Metals",
        "author": "Jing Zhang",
        "tabName": "",
    }
    msg = format_message(item)
    assert "Top News - Ferrous Metals" in msg
    assert "Jing Zhang" in msg
    assert "04/09/2026 13:46 UTC" in msg
    assert "China steel output lags 2025" in msg
    assert "abc123" in msg


def test_format_message_for_flash():
    from execution.curation.telegram_poster import format_message
    item = {
        "id": "def456",
        "title": "Supreme Court strikes down Trump's global tariffs",
        "fullText": "Supreme Court strikes down Trump's global tariffs",
        "publishDate": "02/20/2026 15:09 UTC",
        "source": "allInsights.flash",
        "author": "",
        "tabName": "",
    }
    msg = format_message(item)
    assert "🔴 FLASH" in msg
    assert "02/20/2026 15:09 UTC" in msg


def test_build_keyboard_has_4_buttons():
    from execution.curation.telegram_poster import build_keyboard
    kb = build_keyboard("abc123", preview_url="https://example.com/preview/abc123")
    # kb is dict with "inline_keyboard" = list of rows
    all_buttons = [b for row in kb["inline_keyboard"] for b in row]
    assert len(all_buttons) == 4
    urls = [b.get("url") for b in all_buttons if "url" in b]
    callbacks = [b.get("callback_data") for b in all_buttons if "callback_data" in b]
    assert "https://example.com/preview/abc123" in urls
    assert "curate_archive:abc123" in callbacks
    assert "curate_reject:abc123" in callbacks
    assert "curate_pipeline:abc123" in callbacks


def test_post_for_curation_calls_send_with_keyboard(monkeypatch):
    from execution.curation import telegram_poster
    sent = {}

    def fake_send(chat_id, text, reply_markup=None, parse_mode=None):
        sent["chat_id"] = chat_id
        sent["text"] = text
        sent["reply_markup"] = reply_markup

    monkeypatch.setattr(telegram_poster, "_send_message", fake_send)

    item = {
        "id": "abc123",
        "title": "Test",
        "fullText": "body",
        "publishDate": "date",
        "source": "Top News",
        "author": "",
        "tabName": "",
    }
    telegram_poster.post_for_curation(chat_id=99, item=item, preview_base_url="https://w.example.com")
    assert sent["chat_id"] == 99
    assert "Test" in sent["text"]
    assert sent["reply_markup"] is not None
```

- [ ] **Step 2: Run test, verify fail**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_curation_telegram_poster.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

Create `execution/curation/telegram_poster.py`:

```python
"""Format and send Telegram curation messages.

Each Platts item becomes one Telegram message with:
- Preview (markdown, ~400 chars)
- Inline keyboard: [📖 Ler completo] [✅ Arquivar] [❌ Recusar] [🤖 3 Agents]
"""
import os
from typing import Optional

from execution.integrations.telegram_client import TelegramClient

_PREVIEW_CHAR_LIMIT = 400


def build_preview(text: str, limit: int = _PREVIEW_CHAR_LIMIT) -> str:
    """Truncate text to limit chars, append '...' if truncated."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _is_flash(item: dict) -> bool:
    return (item.get("source") or "").startswith("allInsights.flash")


def format_message(item: dict) -> str:
    """Render a Telegram Markdown message for an item."""
    title = item.get("title", "")
    full_text = item.get("fullText", "")
    publish_date = item.get("publishDate") or item.get("date") or ""
    source = item.get("source", "")
    author = item.get("author", "")
    tab_name = item.get("tabName", "")
    item_id = item.get("id", "")

    preview = build_preview(full_text)

    if _is_flash(item):
        header = f"🔴 *FLASH* {publish_date}\n━━━━━━━━━━━━━━━━━━━━\n"
        title_line = f"*{title}*\n\n" if title and title != full_text else ""
        footer_meta = "━━━━━━━━━━━━━━━━━━━━"
    else:
        header = ""
        title_line = f"*{title}*\n\n"
        meta_lines = [f"📰 {source}"]
        if tab_name:
            meta_lines.append(f"🔖 {tab_name}")
        if author:
            meta_lines.append(f"✍️ {author}")
        if publish_date:
            meta_lines.append(f"📅 {publish_date}")
        meta_lines.append(f"🆔 `{item_id}`")
        footer_meta = "━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(meta_lines)

    return f"{header}{title_line}{preview}\n\n{footer_meta}"


def build_keyboard(item_id: str, preview_url: str) -> dict:
    """Build Telegram inline keyboard: URL button + 3 callback buttons."""
    return {
        "inline_keyboard": [
            [{"text": "📖 Ler completo", "url": preview_url}],
            [
                {"text": "✅ Arquivar", "callback_data": f"curate_archive:{item_id}"},
                {"text": "❌ Recusar", "callback_data": f"curate_reject:{item_id}"},
                {"text": "🤖 3 Agents", "callback_data": f"curate_pipeline:{item_id}"},
            ],
        ]
    }


def _send_message(chat_id: int, text: str, reply_markup: dict, parse_mode: str = "Markdown") -> None:
    """Thin wrapper around TelegramClient for easy mocking in tests."""
    client = TelegramClient()
    client.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode)


def post_for_curation(chat_id: int, item: dict, preview_base_url: str) -> None:
    """Send one curation message for item."""
    text = format_message(item)
    preview_url = f"{preview_base_url.rstrip('/')}/preview/{item['id']}"
    keyboard = build_keyboard(item["id"], preview_url)
    _send_message(chat_id, text, keyboard)
```

- [ ] **Step 4: Check TelegramClient.send_message signature**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && grep -n "def send_message\|def send_approval" execution/integrations/telegram_client.py | head`
Expected: Reveals the actual method. If the method is not `send_message`, update the `_send_message` wrapper to call the real one.

If `TelegramClient` lacks a generic `send_message(chat_id, text, reply_markup, parse_mode)`, replace `_send_message` body with direct HTTP POST to `https://api.telegram.org/bot<TOKEN>/sendMessage`:

```python
def _send_message(chat_id: int, text: str, reply_markup: dict, parse_mode: str = "Markdown") -> None:
    import requests
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "reply_markup": reply_markup,
    }
    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=10)
```

- [ ] **Step 5: Run test, verify pass**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_curation_telegram_poster.py -v`
Expected: 6 tests PASS

- [ ] **Step 6: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/curation/telegram_poster.py tests/test_curation_telegram_poster.py && git commit -m "feat(curation): telegram message formatter + curation button poster"
```

---

## Phase 2: Router

### Task 4: rationale_dispatcher — wrap existing rationale flow

**Files:**
- Create: `execution/curation/rationale_dispatcher.py`

This encapsulates the AI + draft + approval logic from the deprecated `rationale_ingestion.py` so it can be called from inside the unified script.

- [ ] **Step 1: Implement**

Create `execution/curation/rationale_dispatcher.py`:

```python
"""Dispatches rationale items through RationaleAgent + existing approval flow.

Replicates the post-scrape logic from the deprecated rationale_ingestion.py,
stripped of its Apify invocation (caller already has the items).
"""
import json
import os
from datetime import datetime
from typing import List

from execution.agents.rationale_agent import RationaleAgent
from execution.core import state_store
from execution.core.logger import WorkflowLogger

_WORKFLOW_NAME = "rationale_news"
_DRAFTS_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "news_drafts.json")


def _save_draft(draft: dict) -> None:
    """Append draft to news_drafts.json."""
    drafts_path = os.path.abspath(_DRAFTS_FILE)
    os.makedirs(os.path.dirname(drafts_path), exist_ok=True)
    if os.path.exists(drafts_path):
        with open(drafts_path, "r") as f:
            try:
                drafts = json.load(f)
            except (json.JSONDecodeError, ValueError):
                drafts = []
    else:
        drafts = []
    drafts.append(draft)
    with open(drafts_path, "w") as f:
        json.dump(drafts, f, indent=2, ensure_ascii=False)


def process(rationale_items: List[dict], today_br: str, logger: WorkflowLogger = None) -> bool:
    """Run full rationale pipeline on items.

    Returns True if processing succeeded end-to-end (flag should be set),
    False if any step short-circuited without an error that warrants retry.
    Raises on unexpected errors (caller logs + decides on record_crash).
    """
    log = logger or WorkflowLogger("RationaleDispatcher")

    if not rationale_items:
        log.warning("No rationale items to process.")
        state_store.record_empty(_WORKFLOW_NAME, "sem rationales no run")
        return False

    combined_text = "\n\n".join([
        (
            f"=== ARTICLE {i+1} ===\n"
            f"Tab: {item.get('tabName', '')}\n"
            f"Title: {item.get('title')}\n"
            f"Date: {item.get('gridDateTime') or item.get('publishDate') or ''}\n\n"
            f"{item.get('fullText', '')}"
        )
        for i, item in enumerate(rationale_items)
    ])

    if len(combined_text.strip()) < 200:
        log.warning(f"Combined rationale text too short ({len(combined_text)} chars). Skipping AI.")
        state_store.record_empty(_WORKFLOW_NAME, "conteudo insuficiente")
        return False

    log.info(f"Running RationaleAgent on {len(rationale_items)} items...")
    agent = RationaleAgent()
    draft_text = agent.process(combined_text, today_br)

    draft_obj = {
        "id": f"draft_{int(datetime.now().timestamp())}",
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "source_date": today_br,
        "original_count": len(rationale_items),
        "ai_text": draft_text,
        "source_summary": (rationale_items[0].get("title") or "Sem Título") + "...",
    }
    _save_draft(draft_obj)
    log.info("Draft saved.")

    webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL", "")
    if webhook_url:
        import requests
        try:
            requests.post(
                f"{webhook_url}/store-draft",
                json={
                    "draft_id": draft_obj["id"],
                    "message": draft_text,
                    "uazapi_token": os.getenv("UAZAPI_TOKEN", ""),
                    "uazapi_url": os.getenv("UAZAPI_URL", "https://mineralstrading.uazapi.com"),
                },
                timeout=10,
            )
        except Exception as exc:
            log.warning(f"Could not store draft on webhook: {exc}")

    from execution.integrations.telegram_client import TelegramClient
    telegram = TelegramClient()
    telegram.send_approval_request(draft_id=draft_obj["id"], preview_text=draft_text)
    log.info("Telegram approval sent.")

    state_store.record_success(_WORKFLOW_NAME, {"total": 1, "success": 1, "failure": 0}, 0)
    return True
```

- [ ] **Step 2: Sanity-check imports (no test — this is a wrapper of existing logic that needs integration test)**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python3 -c "import ast; ast.parse(open('execution/curation/rationale_dispatcher.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/curation/rationale_dispatcher.py && git commit -m "feat(curation): rationale dispatcher wraps existing AI pipeline"
```

---

### Task 5: router — classify items and dispatch

**Files:**
- Create: `execution/curation/router.py`
- Create: `tests/test_curation_router.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_curation_router.py`:

```python
"""Tests for execution.curation.router."""
import pytest
import fakeredis


@pytest.fixture
def fake_redis(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    from execution.curation import redis_client
    monkeypatch.setattr(redis_client, "_get_client", lambda: fake)
    return fake


def test_classify_rmw_rationale_is_rationale():
    from execution.curation.router import classify
    item = {"source": "rmw.CFR North China Iron Ore 65% Fe Rationale", "tabName": "CFR North China Iron Ore 65% Fe Rationale"}
    assert classify(item) == "rationale"


def test_classify_rmw_iodex_commentary_is_rationale():
    from execution.curation.router import classify
    item = {"source": "rmw.IODEX Commentary and Rationale", "tabName": "IODEX Commentary and Rationale"}
    assert classify(item) == "rationale"


def test_classify_rmw_lump_is_rationale():
    from execution.curation.router import classify
    item = {"source": "rmw.Lump", "tabName": "Lump"}
    assert classify(item) == "rationale"


def test_classify_rmw_bots_is_curation():
    from execution.curation.router import classify
    item = {"source": "rmw.IODEX BOTs and Summary", "tabName": "IODEX BOTs and Summary"}
    assert classify(item) == "curation"


def test_classify_top_news_is_curation():
    from execution.curation.router import classify
    item = {"source": "Top News - Ferrous Metals", "tabName": ""}
    assert classify(item) == "curation"


def test_classify_flash_is_curation():
    from execution.curation.router import classify
    item = {"source": "allInsights.flash", "tabName": ""}
    assert classify(item) == "curation"


def test_route_items_skips_already_seen(fake_redis, monkeypatch):
    """Seen items are neither staged nor posted."""
    from execution.curation import router, redis_client
    from execution.curation.id_gen import generate_id

    posted = []

    def fake_post(chat_id, item, preview_base_url):
        posted.append(item["id"])

    monkeypatch.setattr(router, "_post_for_curation", fake_post)

    item = {"source": "Top News - Ferrous Metals", "title": "Already Seen", "fullText": "x", "tabName": ""}
    item_id = generate_id(item["source"], item["title"])
    redis_client.mark_seen("2026-04-14", item_id)

    router.route_items(
        items=[item],
        today_date="2026-04-14",
        today_br="14/04/2026",
        chat_id=99,
        preview_base_url="https://example.com",
        rationale_processor=lambda rationale_items, today_br: True,
    )
    assert posted == []


def test_route_items_stages_new_curation(fake_redis, monkeypatch):
    from execution.curation import router, redis_client

    posted = []

    def fake_post(chat_id, item, preview_base_url):
        posted.append(item["id"])

    monkeypatch.setattr(router, "_post_for_curation", fake_post)

    item = {"source": "Top News - Ferrous Metals", "title": "Fresh News", "fullText": "x" * 50, "tabName": ""}
    router.route_items(
        items=[item],
        today_date="2026-04-14",
        today_br="14/04/2026",
        chat_id=99,
        preview_base_url="https://example.com",
        rationale_processor=lambda rationale_items, today_br: True,
    )
    # One curation post
    assert len(posted) == 1
    # Item staged in redis
    staged = redis_client.get_staging(posted[0])
    assert staged["title"] == "Fresh News"
    # Item marked seen
    assert redis_client.is_seen("2026-04-14", posted[0]) is True


def test_route_items_dispatches_rationale_once(fake_redis, monkeypatch):
    from execution.curation import router

    rationale_calls = []

    def fake_rationale(rationale_items, today_br):
        rationale_calls.append(len(rationale_items))
        return True

    monkeypatch.setattr(router, "_post_for_curation", lambda *a, **kw: None)

    items = [
        {"source": "rmw.CFR North China Iron Ore 65% Fe Rationale", "tabName": "CFR North China Iron Ore 65% Fe Rationale", "title": "R1", "fullText": "r1"},
        {"source": "rmw.Lump", "tabName": "Lump", "title": "R2", "fullText": "r2"},
    ]
    router.route_items(
        items=items,
        today_date="2026-04-14",
        today_br="14/04/2026",
        chat_id=99,
        preview_base_url="https://example.com",
        rationale_processor=fake_rationale,
    )
    assert rationale_calls == [2]


def test_route_items_skips_rationale_if_already_processed(fake_redis, monkeypatch):
    from execution.curation import router, redis_client
    redis_client.set_rationale_processed("2026-04-14")

    rationale_calls = []

    def fake_rationale(rationale_items, today_br):
        rationale_calls.append(len(rationale_items))
        return True

    monkeypatch.setattr(router, "_post_for_curation", lambda *a, **kw: None)

    items = [
        {"source": "rmw.Lump", "tabName": "Lump", "title": "R1", "fullText": "r1"},
    ]
    router.route_items(
        items=items,
        today_date="2026-04-14",
        today_br="14/04/2026",
        chat_id=99,
        preview_base_url="https://example.com",
        rationale_processor=fake_rationale,
    )
    assert rationale_calls == []
```

- [ ] **Step 2: Run test, verify fail**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_curation_router.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'execution.curation.router'`)

- [ ] **Step 3: Implement**

Create `execution/curation/router.py`:

```python
"""Classify dataset items and dispatch them to rationale AI or Telegram curation."""
import re
from typing import Callable, List

from execution.curation import redis_client
from execution.curation.id_gen import generate_id
from execution.curation.telegram_poster import post_for_curation as _post_for_curation
from execution.core.logger import WorkflowLogger

_RATIONALE_TAB_RE = re.compile(r"Rationale|Lump", re.IGNORECASE)


def classify(item: dict) -> str:
    """Return 'rationale' for RMW Rationale/Lump items, 'curation' otherwise."""
    source = item.get("source") or ""
    tab_name = item.get("tabName") or ""
    if source.startswith("rmw") and _RATIONALE_TAB_RE.search(tab_name):
        return "rationale"
    return "curation"


def _stage_and_post(item: dict, today_date: str, chat_id: int, preview_base_url: str, logger: WorkflowLogger) -> None:
    """Stage one curation item in Redis + post Telegram message."""
    item_id = generate_id(item.get("source", ""), item.get("title", ""))
    if redis_client.is_seen(today_date, item_id):
        logger.info(f"Skipping seen item {item_id} ({item.get('title','')[:40]})")
        return
    item = dict(item)
    item["id"] = item_id
    redis_client.set_staging(item_id, item)
    redis_client.mark_seen(today_date, item_id)
    try:
        _post_for_curation(chat_id=chat_id, item=item, preview_base_url=preview_base_url)
    except Exception as exc:
        logger.warning(f"Telegram post failed for {item_id}: {exc}")


def route_items(
    items: List[dict],
    today_date: str,
    today_br: str,
    chat_id: int,
    preview_base_url: str,
    rationale_processor: Callable[[List[dict], str], bool],
    logger: WorkflowLogger = None,
) -> dict:
    """Split items into rationale/curation buckets and dispatch each.

    rationale_processor: callable(items, today_br) -> bool (True on success)
    Returns counters dict {'total', 'rationale_processed', 'curation_posted', 'skipped_seen'}.
    """
    log = logger or WorkflowLogger("CurationRouter")
    counters = {"total": len(items), "rationale_processed": 0, "curation_posted": 0, "skipped_seen": 0}

    rationale_items: List[dict] = []
    curation_items: List[dict] = []
    for item in items:
        if classify(item) == "rationale":
            rationale_items.append(item)
        else:
            curation_items.append(item)

    # Rationale path: gated by daily flag
    if rationale_items:
        if redis_client.is_rationale_processed(today_date):
            log.info(f"Rationale already processed for {today_date}; skipping {len(rationale_items)} items.")
        else:
            log.info(f"Processing {len(rationale_items)} rationale items...")
            ok = rationale_processor(rationale_items, today_br)
            if ok:
                redis_client.set_rationale_processed(today_date)
                counters["rationale_processed"] = len(rationale_items)

    # Curation path: one Telegram message per new item
    for item in curation_items:
        item_id = generate_id(item.get("source", ""), item.get("title", ""))
        if redis_client.is_seen(today_date, item_id):
            counters["skipped_seen"] += 1
            continue
        _stage_and_post(item, today_date, chat_id, preview_base_url, log)
        counters["curation_posted"] += 1

    return counters
```

- [ ] **Step 4: Run test, verify pass**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_curation_router.py -v`
Expected: 10 tests PASS

- [ ] **Step 5: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/curation/router.py tests/test_curation_router.py && git commit -m "feat(curation): router classifies items and dispatches rationale vs curation"
```

---

## Phase 3: Unified ingestion script

### Task 6: platts_ingestion.py — entry point

**Files:**
- Create: `execution/scripts/platts_ingestion.py`

- [ ] **Step 1: Implement**

Create `execution/scripts/platts_ingestion.py`:

```python
#!/usr/bin/env python3
"""Unified Platts ingestion: scrape → dedup → route to rationale AI or Telegram curation.

Replaces rationale_ingestion.py and market_news_ingestion.py.
Scheduled 3x/day (9h, 12h, 15h BRT) via Railway cron.
"""
import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))

from execution.core import state_store
from execution.core.logger import WorkflowLogger
from execution.curation import rationale_dispatcher, router
from execution.integrations.apify_client import ApifyClient

ACTOR_ID = os.getenv("APIFY_PLATTS_ACTOR_ID", "bigodeio05/platts-scrap-full-news")
WORKFLOW_NAME = "platts_ingestion"


def _flatten_dataset(items: list) -> list:
    """Flatten merged-actor dataset shape into a flat list of article dicts."""
    flat = []
    for item in items:
        if not isinstance(item, dict):
            continue
        # New shape wrapper
        if any(k in item for k in ("topNews", "latest", "newsInsights", "rmw", "flash")):
            for key in ("flash", "topNews", "latest", "newsInsights"):
                flat.extend(item.get(key) or [])
            for group in item.get("rmw") or []:
                tab = group.get("tabName", "")
                for a in group.get("articles") or []:
                    a = dict(a)
                    a.setdefault("tabName", tab)
                    flat.append(a)
        else:
            # Already a single article
            flat.append(item)
    return flat


def main():
    logger = WorkflowLogger("PlattsIngestion")
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Skip Apify, use mock data")
    parser.add_argument("--target-date", type=str, default="",
                        help="Data alvo DD/MM/YYYY. Vazio = hoje.")
    args = parser.parse_args()

    try:
        if args.target_date:
            today_br = args.target_date
            try:
                date_iso = datetime.strptime(today_br, "%d/%m/%Y").strftime("%Y-%m-%d")
            except ValueError:
                logger.error(f"Invalid date: {today_br}. Expected DD/MM/YYYY")
                sys.exit(1)
        else:
            today_br = datetime.now().strftime("%d/%m/%Y")
            date_iso = datetime.now().strftime("%Y-%m-%d")
        logger.info(f"Starting ingestion for date: {today_br} (iso: {date_iso})")

        chat_id = int(os.getenv("TELEGRAM_CHAT_ID", "0"))
        preview_base_url = os.getenv("TELEGRAM_WEBHOOK_URL", "")
        if not chat_id or not preview_base_url:
            logger.error("TELEGRAM_CHAT_ID or TELEGRAM_WEBHOOK_URL not set.")
            sys.exit(1)

        run_input = {
            "username": os.getenv("PLATTS_USERNAME", ""),
            "password": os.getenv("PLATTS_PASSWORD", ""),
            "sources": ["allInsights", "ironOreTopic", "rmw"],
            "includeFlash": True,
            "includeLatest": True,
            "maxArticlesPerRmwTab": 5,
            "latestMaxItems": 15,
            "dateFilter": "today",
            "concurrency": 2,
            "dedupArticles": True,
        }
        if args.target_date:
            run_input["targetDate"] = args.target_date
            run_input["dateFormat"] = "BR"
            run_input["dateFilter"] = "all"

        if args.dry_run:
            logger.info("[DRY RUN] Would run Apify with input: " + str(run_input))
            items = [{
                "type": "success",
                "topNews": [{
                    "title": "DryRun Test Item",
                    "fullText": "Test body with some prices $104.80/dmt CFR.",
                    "publishDate": today_br,
                    "source": "Top News - Ferrous Metals",
                    "author": "Test Author",
                    "tabName": "",
                }],
                "rmw": [],
                "summary": {"totalArticles": 1},
            }]
        else:
            logger.info(f"Running Apify Actor: {ACTOR_ID}")
            client = ApifyClient()
            dataset_id = client.run_actor(ACTOR_ID, run_input, memory_mbytes=2048)
            items = client.get_dataset_items(dataset_id)

        articles = _flatten_dataset(items)
        logger.info(f"Flattened to {len(articles)} articles.")

        if not articles:
            logger.warning("No articles after flatten.")
            state_store.record_empty(WORKFLOW_NAME, "scrape vazio")
            return

        def rationale_processor(rationale_items, today_br_inner):
            return rationale_dispatcher.process(rationale_items, today_br_inner, logger=logger)

        counters = router.route_items(
            items=articles,
            today_date=date_iso,
            today_br=today_br,
            chat_id=chat_id,
            preview_base_url=preview_base_url,
            rationale_processor=rationale_processor,
            logger=logger,
        )
        logger.info(f"Route summary: {counters}")
        state_store.record_success(WORKFLOW_NAME, counters, 0)

    except Exception as e:
        logger.critical(f"Workflow failed: {e}")
        state_store.record_crash(WORKFLOW_NAME, str(e)[:200])
        raise


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Syntax check**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python3 -c "import ast; ast.parse(open('execution/scripts/platts_ingestion.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Dry-run smoke test**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && TELEGRAM_CHAT_ID=1 TELEGRAM_WEBHOOK_URL=https://example.com REDIS_URL=redis://invalid:6379/0 python3 execution/scripts/platts_ingestion.py --dry-run 2>&1 | tail -20`
Expected: Script runs, fails on Redis (which is fine — confirms wiring). Look for the log line "Flattened to 1 articles." before the Redis error.

- [ ] **Step 4: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/scripts/platts_ingestion.py && git commit -m "feat(curation): unified platts_ingestion script"
```

---

## Phase 4: Webhook additions

### Task 7: preview HTML route + Jinja template

**Files:**
- Create: `webhook/templates/preview.html`
- Modify: `webhook/app.py` (add route + template loader)

- [ ] **Step 1: Find Flask app init in webhook/app.py to understand template folder config**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && grep -n "Flask\|template_folder\|render_template" webhook/app.py | head -10`
Expected: Reveals `Flask(__name__)` line. Default template folder is `templates/` relative to the `.py` file — so `webhook/templates/` is correct.

- [ ] **Step 2: Create the Jinja template**

Create `webhook/templates/preview.html`:

```html
<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{{ item.title | default("Platts — Preview") }}</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-900 text-slate-100 min-h-screen">
  <main class="max-w-3xl mx-auto p-6">
    <article class="space-y-6">
      <header class="space-y-3 border-b border-slate-700 pb-4">
        <h1 class="text-2xl font-bold text-white">{{ item.title }}</h1>
        <dl class="grid grid-cols-2 gap-2 text-sm text-slate-400">
          {% if item.source %}
          <div><dt class="inline font-semibold">Fonte:</dt> <dd class="inline">{{ item.source }}</dd></div>
          {% endif %}
          {% if item.tabName %}
          <div><dt class="inline font-semibold">Tab:</dt> <dd class="inline">{{ item.tabName }}</dd></div>
          {% endif %}
          {% if item.author %}
          <div><dt class="inline font-semibold">Autor:</dt> <dd class="inline">{{ item.author }}</dd></div>
          {% endif %}
          {% if item.publishDate %}
          <div><dt class="inline font-semibold">Data:</dt> <dd class="inline">{{ item.publishDate }}</dd></div>
          {% endif %}
        </dl>
      </header>

      <section class="prose prose-invert max-w-none">
        {% for paragraph in item.fullText.split("\n\n") %}
          {% if paragraph.strip() %}
            <p class="text-slate-200 leading-relaxed">{{ paragraph }}</p>
          {% endif %}
        {% endfor %}
      </section>

      {% if item.tables %}
      <section>
        <h2 class="text-lg font-semibold text-white mb-3">Tabelas</h2>
        {% for table in item.tables %}
          <table class="w-full text-sm border border-slate-700 mb-4">
            {% if table.headers %}
            <thead class="bg-slate-800">
              <tr>
                {% for h in table.headers %}<th class="px-3 py-2 text-left border border-slate-700">{{ h }}</th>{% endfor %}
              </tr>
            </thead>
            {% endif %}
            <tbody>
              {% for row in table.rows %}
              <tr class="border-t border-slate-700">
                {% for cell in row %}<td class="px-3 py-2 border border-slate-700">{{ cell }}</td>{% endfor %}
              </tr>
              {% endfor %}
            </tbody>
          </table>
        {% endfor %}
      </section>
      {% endif %}

      {% if item.url %}
      <footer class="pt-4 border-t border-slate-700">
        <a href="{{ item.url }}" target="_blank" rel="noopener"
           class="text-sky-400 underline hover:text-sky-300">Abrir no Platts →</a>
      </footer>
      {% endif %}
    </article>
  </main>
</body>
</html>
```

- [ ] **Step 3: Add preview route to webhook/app.py**

Find the first `@app.route(...)` definition (around line 1030 — `/health`). Above it, add the new route:

Open `webhook/app.py` and add:

```python
@app.route("/preview/<item_id>", methods=["GET"])
def preview_item(item_id):
    """Render Platts item HTML preview for Telegram in-app browser.

    Looks up item in Redis staging first, then in today's archive,
    then falls back to a 404 message.
    """
    from flask import render_template, abort
    from datetime import datetime
    from execution.curation import redis_client

    try:
        item = redis_client.get_staging(item_id)
    except Exception as exc:
        logger.warning(f"Preview redis staging lookup failed: {exc}")
        item = None

    if item is None:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        try:
            client = redis_client._get_client()
            raw = client.get(f"platts:archive:{today}:{item_id}")
            if raw:
                import json
                item = json.loads(raw)
        except Exception as exc:
            logger.warning(f"Preview redis archive lookup failed: {exc}")

    if item is None:
        return "<h1>Item not found</h1><p>Expired (48h) or already processed.</p>", 404

    return render_template("preview.html", item=item)
```

The exact line number to insert above is `@app.route("/health", methods=["GET"])`. Use Edit tool with the existing line as `old_string` anchor.

- [ ] **Step 4: Manual smoke test**

In one terminal, start the webhook:
```
cd "/Users/bigode/Dev/Antigravity WF " && python3 -m webhook.app
```
(Or whatever the existing start command is — check `webhook/app.py` last lines and Railway config.)

In another terminal, seed a test item into Redis:
```
redis-cli -u "$REDIS_URL" SET platts:staging:testid '{"id":"testid","title":"Test","fullText":"Paragraph 1.\n\nParagraph 2.","source":"Test Source","publishDate":"2026-04-14","tabName":"","author":"","url":"https://example.com"}' EX 60
```
Then curl the preview:
```
curl -s http://localhost:5000/preview/testid | head -40
```
Expected: HTML containing `Test` as title and paragraphs rendered.

- [ ] **Step 5: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add webhook/templates/preview.html webhook/app.py && git commit -m "feat(curation): add /preview/<id> HTML route with Jinja+Tailwind"
```

---

### Task 8: 3 new callback handlers (curate_archive, curate_reject, curate_pipeline)

**Files:**
- Modify: `webhook/app.py`

- [ ] **Step 1: Locate handle_callback dispatch logic**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && grep -n "def handle_callback\|parts = callback_data.split\|elif action ==" webhook/app.py | head -20`
Expected: You'll see `parts = callback_data.split(":", 1)` at ~line 1292 and the `if action == "approve":` chain starting at 1299.

- [ ] **Step 2: Add the 3 new handler branches**

In `webhook/app.py`, find the last `elif action == ...` branch in `handle_callback()` (it ends with the chain handling `approve`/`test_approve`/`adjust`/`reject`). Immediately before the function's final `else:` or return, add:

```python
    elif action == "curate_archive":
        item_id = parts[1] if len(parts) > 1 else ""
        from datetime import datetime
        from execution.curation import redis_client
        date = datetime.utcnow().strftime("%Y-%m-%d")
        try:
            archived = redis_client.archive(item_id, date, chat_id=chat_id)
        except Exception as exc:
            logger.error(f"curate_archive redis error: {exc}")
            answer_callback(callback_query["id"], "⚠️ Redis indisponível, tenta de novo")
            return jsonify({"ok": True})
        if archived is None:
            answer_callback(callback_query["id"], "⚠️ Item expirou ou já processado")
            edit_message(chat_id, callback_query["message"]["message_id"], "⚠️ Item expirou ou já processado", reply_markup=None)
            return jsonify({"ok": True})
        answer_callback(callback_query["id"], "✅ Arquivado")
        edit_message(
            chat_id,
            callback_query["message"]["message_id"],
            f"✅ *Arquivado* em {datetime.now().strftime('%H:%M')}\n🆔 `{item_id}`",
            reply_markup=None,
        )
        return jsonify({"ok": True})

    elif action == "curate_reject":
        item_id = parts[1] if len(parts) > 1 else ""
        from datetime import datetime
        from execution.curation import redis_client
        try:
            redis_client.discard(item_id)
        except Exception as exc:
            logger.error(f"curate_reject redis error: {exc}")
            answer_callback(callback_query["id"], "⚠️ Redis indisponível")
            return jsonify({"ok": True})
        answer_callback(callback_query["id"], "❌ Recusado")
        edit_message(
            chat_id,
            callback_query["message"]["message_id"],
            f"❌ *Recusado* em {datetime.now().strftime('%H:%M')}\n🆔 `{item_id}`",
            reply_markup=None,
        )
        return jsonify({"ok": True})

    elif action == "curate_pipeline":
        item_id = parts[1] if len(parts) > 1 else ""
        from datetime import datetime
        from execution.curation import redis_client
        try:
            item = redis_client.get_staging(item_id)
        except Exception as exc:
            logger.error(f"curate_pipeline redis error: {exc}")
            answer_callback(callback_query["id"], "⚠️ Redis indisponível")
            return jsonify({"ok": True})
        if item is None:
            answer_callback(callback_query["id"], "⚠️ Item expirou")
            edit_message(chat_id, callback_query["message"]["message_id"], "⚠️ Item expirou ou já processado", reply_markup=None)
            return jsonify({"ok": True})
        raw_text = (
            f"Title: {item.get('title','')}\n"
            f"Date: {item.get('publishDate','')}\n"
            f"Source: {item.get('source','')}\n\n"
            f"{item.get('fullText','')}"
        )
        answer_callback(callback_query["id"], "🤖 Processando nos 3 agents...")
        progress = send_telegram_message(chat_id, f"🤖 Processando item `{item_id}` nos 3 agents...")
        progress_msg_id = progress.get("result", {}).get("message_id") if progress else None
        import threading
        threading.Thread(
            target=process_news_async,
            args=(chat_id, raw_text, progress_msg_id),
            daemon=True,
        ).start()
        # Archive the item so it moves out of staging (decision taken)
        date = datetime.utcnow().strftime("%Y-%m-%d")
        try:
            redis_client.archive(item_id, date, chat_id=chat_id)
        except Exception as exc:
            logger.warning(f"curate_pipeline archive post-dispatch failed: {exc}")
        edit_message(
            chat_id,
            callback_query["message"]["message_id"],
            f"🤖 *Enviado aos 3 agents* em {datetime.now().strftime('%H:%M')}\n🆔 `{item_id}`",
            reply_markup=None,
        )
        return jsonify({"ok": True})
```

Use the Edit tool to insert this block. Anchor: find the last existing `elif action == "reject":` block and add these three new branches right after it.

- [ ] **Step 3: Lint / syntax check**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python3 -c "import ast; ast.parse(open('webhook/app.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Manual integration smoke (optional, requires full stack)**

Start webhook. Seed a staging item:
```
redis-cli -u "$REDIS_URL" SET platts:staging:smoketest '{"id":"smoketest","title":"Smoke","fullText":"body","source":"Top News","publishDate":"2026-04-14","author":"","tabName":""}' EX 300
```
Simulate a Telegram callback via `curl`:
```
curl -X POST http://localhost:5000/webhook -H "Content-Type: application/json" -d '{
  "callback_query": {
    "id": "cb1",
    "data": "curate_reject:smoketest",
    "from": {"id": 123},
    "message": {"message_id": 1, "chat": {"id": 123}}
  }
}'
```
Expected: Webhook logs `Callback: curate_reject:smoketest`, Redis no longer has `platts:staging:smoketest`, Telegram edit fails locally (bot token absent) but that's fine — we're testing the branch logic, not the send.

- [ ] **Step 5: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add webhook/app.py && git commit -m "feat(curation): 3 new callback handlers for archive/reject/pipeline"
```

---

## Phase 5: Cleanup + migration

### Task 9: Remove deprecated files

**Files:**
- Delete: `execution/agents/market_news_agent.py`
- Delete: `execution/scripts/market_news_ingestion.py`
- Delete: `execution/scripts/rationale_ingestion.py`

- [ ] **Step 1: Confirm no other references**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && grep -rln "market_news_agent\|market_news_ingestion\|rationale_ingestion" --include="*.py" --include="*.json" --include="*.md" . | grep -v __pycache__ | grep -v .venv | grep -v docs/superpowers`
Expected: Only the files we're deleting + possibly a cron config to update.

If the cron config (e.g., `railway.json`, `.github/workflows/*`, or an internal scheduler config) references these scripts, note the exact path — you'll update it in the next step.

- [ ] **Step 2: Update any cron references**

If a cron/scheduler config still calls `rationale_ingestion.py` or `market_news_ingestion.py`, replace with:
- Single command: `python3 -m execution.scripts.platts_ingestion`
- Schedule: `0 12,15,18 * * *` UTC (9h, 12h, 15h BRT)

Use `grep` output from Step 1 to find the exact file. If none found, note this in the commit message.

- [ ] **Step 3: Delete the files**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git rm execution/agents/market_news_agent.py execution/scripts/market_news_ingestion.py execution/scripts/rationale_ingestion.py
```

- [ ] **Step 4: Run full test suite**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest -q 2>&1 | tail -20`
Expected: All tests pass. If any fail due to broken imports, fix them (search with `grep -rln "market_news_agent\|MarketNewsAgent" tests/`).

- [ ] **Step 5: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git commit -m "chore(curation): remove deprecated rationale_ingestion, market_news_ingestion, MarketNewsAgent"
```

---

### Task 10: End-to-end manual validation

Nothing to implement — this is the validation checklist before deploy. Run it with real Apify + Redis + Telegram (your personal chat as `TELEGRAM_CHAT_ID`, staging bot if you have one).

- [ ] **Step 1: Environment check**

Confirm envs set: `REDIS_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_WEBHOOK_URL` (public HTTPS of webhook), `PLATTS_USERNAME`, `PLATTS_PASSWORD`, `APIFY_TOKEN`.

- [ ] **Step 2: Run ingestion once with full pipeline**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python3 -m execution.scripts.platts_ingestion`
Expected: Logs show scrape → flatten → route summary with counters. Telegram receives N messages (one per non-rationale item) + 1 rationale draft approval message.

- [ ] **Step 3: Per-button test**

Click each button on different items:
- `📖 Ler completo` → opens `/preview/<id>` in Telegram in-app browser, shows full article + tables (if any).
- `✅ Arquivar` on item A → msg edits to "✅ Arquivado em HH:MM". Verify `redis-cli KEYS 'platts:archive:*:<id>'` finds it and `KEYS 'platts:staging:<id>'` is empty.
- `❌ Recusar` on item B → msg edits to "❌ Recusado em HH:MM". Verify `KEYS 'platts:staging:<id>'` empty and NOT in archive.
- `🤖 3 Agents` on item C → msg edits to "🤖 Enviado aos 3 agents", new progress message appears, then draft approval message. Approve it → WhatsApp dispatched via UAZapi. Verify archived in Redis.

- [ ] **Step 4: Dedup test**

Run ingestion a second time within 60s: `python3 -m execution.scripts.platts_ingestion`
Expected: Log shows `skipped_seen` counter increased, no duplicate Telegram messages.

- [ ] **Step 5: Rationale gate test**

Run ingestion a third time same day.
Expected: Rationale path skipped with log "Rationale already processed for YYYY-MM-DD".

- [ ] **Step 6: Expired preview test**

Manually delete a staging key: `redis-cli DEL platts:staging:<id>`. Click `📖 Ler completo` for that item → preview page shows "Item not found — expired or processed" 404 HTML.

- [ ] **Step 7: Final sign-off commit**

If everything works, nothing to commit — implementation is complete. Announce done.

---

## Self-review pass

**Spec coverage check:**
- [x] Redis schema (staging 48h / archive no-TTL / seen 30d / rationale-flag 30h) → Task 2
- [x] ID generation sha256 12-char → Task 1
- [x] Telegram message format (FLASH vs normal) → Task 3
- [x] 4 buttons (preview URL + 3 callbacks) → Task 3 keyboard + Task 8 handlers
- [x] Rationale dispatcher wraps RationaleAgent + draft + approval → Task 4
- [x] Router classification + gating → Task 5
- [x] Unified ingestion script replaces two old ones → Task 6
- [x] Preview HTML route + Jinja template → Task 7
- [x] 3 callback handlers (archive/reject/pipeline) → Task 8
- [x] Cleanup of deprecated files → Task 9
- [x] End-to-end manual validation (dedup, rationale gate, expired) → Task 10
- [x] Error handling (Redis down, missing item, duplicate click) → Task 8 handler branches all have try/except or staging-missing checks

**Placeholder scan:** No TBDs, no "implement later", every code block has real code. `WorkflowLogger("...")` names are concrete, not placeholders.

**Type/name consistency:**
- `generate_id(source, title)` used in Tasks 1, 5 (test + router) ✓
- `set_staging / get_staging / archive / discard / is_seen / mark_seen / is_rationale_processed / set_rationale_processed` match between Tasks 2, 5, 8 ✓
- `build_preview / format_message / build_keyboard / post_for_curation` match between Task 3 impl and Task 5 router import ✓
- Redis keys `platts:staging:<id>` / `platts:archive:<date>:<id>` / `platts:seen:<date>` / `platts:rationale:processed:<date>` consistent across all tasks ✓
- Callback data strings `curate_archive:<id>` / `curate_reject:<id>` / `curate_pipeline:<id>` match between Task 3 (builder) and Task 8 (handlers) ✓
