# /queue Bulk Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a select mode to `/queue` that lets the admin mark multiple staging items across pages and archive-or-discard them with one confirmation.

**Architecture:** Volatile per-chat selection state in Redis (10 min TTL, two keys: a mode flag and a set of selected ids). `format_queue_page` gains a `mode`/`selected` branch. Seven new callback handlers (mode toggle, item toggle, select-all, clear, bulk-prompt, bulk-confirm, bulk-cancel) wired to the existing `callbacks_queue_router`. Bulk archive/discard are thin wrappers over the existing single-item `archive()`/`discard()` that report per-item success/failure.

**Tech Stack:** Python 3.9 (compatible with current test env), aiogram 3, Redis (real in prod via REDIS_URL, fakeredis in tests), pytest + pytest-asyncio + pytest-mock.

**Spec:** `docs/superpowers/specs/2026-04-22-bot-queue-bulk-actions-design.md`

---

## File Map

| File | Role |
|---|---|
| `webhook/queue_selection.py` (new) | Redis-backed state module: enter/exit mode, toggle/select-all/clear, get-selection. Pure I/O. |
| `execution/curation/redis_client.py` (modify) | Add `bulk_archive(ids, date, chat_id)` and `bulk_discard(ids)`. |
| `webhook/bot/callback_data.py` (modify) | Add 7 new `CallbackData` classes for the select-mode flow. |
| `webhook/query_handlers.py` (modify) | Extend `format_queue_page` with `mode`/`selected` args; add select-mode rendering branch and entry button. |
| `webhook/bot/routers/callbacks_queue.py` (modify) | Add 7 handlers; update existing `on_queue_page` to honor current mode. |
| `tests/test_queue_selection.py` (new) | Unit tests for the selection module. |
| `tests/test_curation_redis_client.py` (modify) | Tests for `bulk_archive` + `bulk_discard`. |
| `tests/test_query_handlers.py` (modify) | Tests for select-mode rendering and the entry button. |
| `tests/test_callbacks_queue.py` (modify) | Tests for the 7 new handlers + updated page handler. |

---

## Task 1: Selection state module (`webhook/queue_selection.py`)

**Files:**
- Create: `webhook/queue_selection.py`
- Test: `tests/test_queue_selection.py`

This is a thin wrapper over Redis. It uses `execution.curation.redis_client._get_client` (same Redis as staging) so the fakeredis monkeypatch pattern works in tests.

- [ ] **Step 1.1: Write the failing tests**

Create `tests/test_queue_selection.py` with:

```python
"""Tests for webhook.queue_selection — per-chat select-mode state in Redis."""
import pytest
import fakeredis


@pytest.fixture
def fake_redis(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    from execution.curation import redis_client
    monkeypatch.setattr(redis_client, "_get_client", lambda: fake)
    monkeypatch.setattr(redis_client, "_client", None)
    return fake


def test_mode_absent_by_default(fake_redis):
    from webhook.queue_selection import is_select_mode
    assert is_select_mode(42) is False


def test_enter_mode_sets_flag_and_empty_selection(fake_redis):
    from webhook.queue_selection import enter_mode, is_select_mode, get_selection
    enter_mode(42)
    assert is_select_mode(42) is True
    assert get_selection(42) == set()


def test_enter_mode_is_per_chat(fake_redis):
    from webhook.queue_selection import enter_mode, is_select_mode
    enter_mode(42)
    assert is_select_mode(99) is False


def test_toggle_adds_then_removes(fake_redis):
    from webhook.queue_selection import enter_mode, toggle, get_selection
    enter_mode(42)
    assert toggle(42, "a") is True       # now selected
    assert get_selection(42) == {"a"}
    assert toggle(42, "a") is False      # now unselected
    assert get_selection(42) == set()


def test_toggle_multiple_ids(fake_redis):
    from webhook.queue_selection import enter_mode, toggle, get_selection
    enter_mode(42)
    toggle(42, "a")
    toggle(42, "b")
    toggle(42, "c")
    assert get_selection(42) == {"a", "b", "c"}


def test_select_all_overwrites_existing(fake_redis):
    from webhook.queue_selection import enter_mode, toggle, select_all, get_selection
    enter_mode(42)
    toggle(42, "a")
    select_all(42, ["x", "y", "z"])
    assert get_selection(42) == {"x", "y", "z"}


def test_clear_empties_selection_but_keeps_mode(fake_redis):
    from webhook.queue_selection import enter_mode, toggle, clear, get_selection, is_select_mode
    enter_mode(42)
    toggle(42, "a")
    clear(42)
    assert get_selection(42) == set()
    assert is_select_mode(42) is True


def test_exit_mode_deletes_both_keys(fake_redis):
    from webhook.queue_selection import enter_mode, toggle, exit_mode, is_select_mode, get_selection
    enter_mode(42)
    toggle(42, "a")
    exit_mode(42)
    assert is_select_mode(42) is False
    assert get_selection(42) == set()


def test_enter_mode_sets_ttl(fake_redis):
    from webhook.queue_selection import enter_mode, _TTL_SECONDS
    enter_mode(42)
    mode_ttl = fake_redis.ttl("bot:queue_mode:42")
    assert 0 < mode_ttl <= _TTL_SECONDS


def test_toggle_refreshes_ttl(fake_redis):
    from webhook.queue_selection import enter_mode, toggle, _TTL_SECONDS
    enter_mode(42)
    # Simulate partial TTL burn
    fake_redis.expire("bot:queue_mode:42", 5)
    toggle(42, "a")
    mode_ttl = fake_redis.ttl("bot:queue_mode:42")
    assert mode_ttl > 5
    sel_ttl = fake_redis.ttl("bot:queue_selected:42")
    assert 0 < sel_ttl <= _TTL_SECONDS


def test_get_selection_returns_empty_when_mode_absent(fake_redis):
    from webhook.queue_selection import get_selection
    assert get_selection(42) == set()
```

- [ ] **Step 1.2: Run tests — verify they fail**

```bash
uv run pytest tests/test_queue_selection.py -v
```

Expected: all tests FAIL (module does not exist).

- [ ] **Step 1.3: Create the module**

Create `webhook/queue_selection.py`:

```python
"""Per-chat select-mode state for /queue bulk actions.

Uses the same Redis instance as the curation keyspace (via
execution.curation.redis_client._get_client). Two keys per chat:

- bot:queue_mode:{chat_id}      string, value "select" when active
- bot:queue_selected:{chat_id}  set of staging item ids

Both keys share a 10 minute TTL and are refreshed on every mutation.
Exiting the mode deletes both keys. The state is volatile by design —
bot restarts discard it.
"""
from __future__ import annotations

from execution.curation import redis_client

_TTL_SECONDS = 10 * 60
_MODE_VALUE = "select"


def _mode_key(chat_id: int) -> str:
    return f"bot:queue_mode:{chat_id}"


def _selected_key(chat_id: int) -> str:
    return f"bot:queue_selected:{chat_id}"


def _refresh_ttl(pipe, chat_id: int) -> None:
    pipe.expire(_mode_key(chat_id), _TTL_SECONDS)
    pipe.expire(_selected_key(chat_id), _TTL_SECONDS)


def is_select_mode(chat_id: int) -> bool:
    client = redis_client._get_client()
    return client.get(_mode_key(chat_id)) == _MODE_VALUE


def enter_mode(chat_id: int) -> None:
    client = redis_client._get_client()
    pipe = client.pipeline()
    pipe.set(_mode_key(chat_id), _MODE_VALUE, ex=_TTL_SECONDS)
    pipe.delete(_selected_key(chat_id))
    pipe.execute()


def exit_mode(chat_id: int) -> None:
    client = redis_client._get_client()
    pipe = client.pipeline()
    pipe.delete(_mode_key(chat_id))
    pipe.delete(_selected_key(chat_id))
    pipe.execute()


def get_selection(chat_id: int) -> set[str]:
    client = redis_client._get_client()
    members = client.smembers(_selected_key(chat_id))
    return set(members) if members else set()


def toggle(chat_id: int, item_id: str) -> bool:
    """Toggle item_id in the selection. Returns True if now selected."""
    client = redis_client._get_client()
    selected_key = _selected_key(chat_id)
    if client.sismember(selected_key, item_id):
        pipe = client.pipeline()
        pipe.srem(selected_key, item_id)
        _refresh_ttl(pipe, chat_id)
        pipe.execute()
        return False
    pipe = client.pipeline()
    pipe.sadd(selected_key, item_id)
    _refresh_ttl(pipe, chat_id)
    pipe.execute()
    return True


def select_all(chat_id: int, item_ids: list[str]) -> None:
    client = redis_client._get_client()
    selected_key = _selected_key(chat_id)
    pipe = client.pipeline()
    pipe.delete(selected_key)
    if item_ids:
        pipe.sadd(selected_key, *item_ids)
    _refresh_ttl(pipe, chat_id)
    pipe.execute()


def clear(chat_id: int) -> None:
    client = redis_client._get_client()
    pipe = client.pipeline()
    pipe.delete(_selected_key(chat_id))
    _refresh_ttl(pipe, chat_id)
    pipe.execute()
```

- [ ] **Step 1.4: Run tests — verify they pass**

```bash
uv run pytest tests/test_queue_selection.py -v
```

Expected: all 10 tests PASS.

- [ ] **Step 1.5: Commit**

```bash
git add webhook/queue_selection.py tests/test_queue_selection.py
git commit -m "feat(queue): add queue_selection state module for bulk actions"
```

---

## Task 2: Bulk archive + discard (`execution/curation/redis_client.py`)

**Files:**
- Modify: `execution/curation/redis_client.py` (append two functions after `discard()`)
- Test: `tests/test_curation_redis_client.py` (append tests)

- [ ] **Step 2.1: Write the failing tests**

Append to `tests/test_curation_redis_client.py`:

```python
def test_bulk_archive_moves_all_present_items(fake_redis):
    from execution.curation.redis_client import set_staging, bulk_archive
    set_staging("a", {"id": "a", "title": "A"})
    set_staging("b", {"id": "b", "title": "B"})
    result = bulk_archive(["a", "b"], "2026-04-22", chat_id=42)
    assert result == {"archived": ["a", "b"], "failed": []}
    assert fake_redis.get("platts:staging:a") is None
    assert fake_redis.get("platts:staging:b") is None
    assert fake_redis.get("platts:archive:2026-04-22:a") is not None
    assert fake_redis.get("platts:archive:2026-04-22:b") is not None


def test_bulk_archive_reports_missing_items_as_failed(fake_redis):
    from execution.curation.redis_client import set_staging, bulk_archive
    set_staging("a", {"id": "a"})
    result = bulk_archive(["a", "missing"], "2026-04-22", chat_id=42)
    assert result["archived"] == ["a"]
    assert result["failed"] == ["missing"]


def test_bulk_archive_empty_list_is_noop(fake_redis):
    from execution.curation.redis_client import bulk_archive
    assert bulk_archive([], "2026-04-22", chat_id=42) == {"archived": [], "failed": []}


def test_bulk_archive_preserves_order(fake_redis):
    from execution.curation.redis_client import set_staging, bulk_archive
    for id_ in ("z", "a", "m"):
        set_staging(id_, {"id": id_})
    result = bulk_archive(["z", "a", "m"], "2026-04-22", chat_id=42)
    assert result["archived"] == ["z", "a", "m"]


def test_bulk_discard_deletes_present_returns_count(fake_redis):
    from execution.curation.redis_client import set_staging, bulk_discard
    set_staging("a", {"id": "a"})
    set_staging("b", {"id": "b"})
    count = bulk_discard(["a", "b", "missing"])
    assert count == 2
    assert fake_redis.get("platts:staging:a") is None
    assert fake_redis.get("platts:staging:b") is None


def test_bulk_discard_empty_list_is_noop(fake_redis):
    from execution.curation.redis_client import bulk_discard
    assert bulk_discard([]) == 0
```

- [ ] **Step 2.2: Run tests — verify they fail**

```bash
uv run pytest tests/test_curation_redis_client.py -v -k "bulk_"
```

Expected: 6 tests FAIL with `ImportError` (`bulk_archive`/`bulk_discard` not defined).

- [ ] **Step 2.3: Add the two functions**

Append to `execution/curation/redis_client.py` after the `discard()` function (line 115):

```python
def bulk_archive(item_ids: list[str], date: str, chat_id: int) -> dict:
    """Archive multiple items. Returns {'archived': [...ids], 'failed': [...ids]}.

    Each item is archived independently via archive() — one missing or
    errored item does not affect the others. Order of 'archived' and
    'failed' reflects input order.
    """
    archived: list[str] = []
    failed: list[str] = []
    for item_id in item_ids:
        try:
            result = archive(item_id, date, chat_id=chat_id)
        except Exception:
            failed.append(item_id)
            continue
        if result is None:
            failed.append(item_id)
        else:
            archived.append(item_id)
    return {"archived": archived, "failed": failed}


def bulk_discard(item_ids: list[str]) -> int:
    """Delete multiple staging keys. Returns count of keys actually deleted.

    Missing keys are silently skipped (deleted count does not include them).
    """
    if not item_ids:
        return 0
    client = _get_client()
    keys = [_staging_key(i) for i in item_ids]
    return int(client.delete(*keys))
```

- [ ] **Step 2.4: Run tests — verify they pass**

```bash
uv run pytest tests/test_curation_redis_client.py -v -k "bulk_"
```

Expected: 6 tests PASS.

- [ ] **Step 2.5: Commit**

```bash
git add execution/curation/redis_client.py tests/test_curation_redis_client.py
git commit -m "feat(curation): add bulk_archive and bulk_discard helpers"
```

---

## Task 3: CallbackData classes

**Files:**
- Modify: `webhook/bot/callback_data.py` (append)
- Test: `tests/test_bot_callback_data.py` (verify round-trip)

- [ ] **Step 3.1: Write the failing test**

Append to `tests/test_bot_callback_data.py` (or create if missing — check first with `ls tests/test_bot_callback_data.py`):

```python
def test_queue_mode_toggle_roundtrip():
    from bot.callback_data import QueueModeToggle
    from aiogram.filters.callback_data import CallbackData
    data = QueueModeToggle(action="enter")
    packed = data.pack()
    assert packed.startswith("q_mode:")
    assert CallbackData.unpack(QueueModeToggle, packed).action == "enter"


def test_queue_sel_toggle_roundtrip():
    from bot.callback_data import QueueSelToggle
    from aiogram.filters.callback_data import CallbackData
    data = QueueSelToggle(item_id="abc123")
    packed = data.pack()
    assert packed.startswith("q_sel:")
    assert CallbackData.unpack(QueueSelToggle, packed).item_id == "abc123"


def test_queue_sel_all_roundtrip():
    from bot.callback_data import QueueSelAll
    packed = QueueSelAll().pack()
    assert packed == "q_all"


def test_queue_sel_none_roundtrip():
    from bot.callback_data import QueueSelNone
    packed = QueueSelNone().pack()
    assert packed == "q_none"


def test_queue_bulk_prompt_roundtrip():
    from bot.callback_data import QueueBulkPrompt
    from aiogram.filters.callback_data import CallbackData
    packed = QueueBulkPrompt(action="archive").pack()
    assert packed.startswith("q_bulk:")
    assert CallbackData.unpack(QueueBulkPrompt, packed).action == "archive"


def test_queue_bulk_confirm_roundtrip():
    from bot.callback_data import QueueBulkConfirm
    from aiogram.filters.callback_data import CallbackData
    packed = QueueBulkConfirm(action="discard").pack()
    assert packed.startswith("q_bulkok:")
    assert CallbackData.unpack(QueueBulkConfirm, packed).action == "discard"


def test_queue_bulk_cancel_roundtrip():
    from bot.callback_data import QueueBulkCancel
    packed = QueueBulkCancel().pack()
    assert packed == "q_bulkno"
```

If `tests/test_bot_callback_data.py` doesn't exist, create it with a normal module header:

```python
"""Tests for aiogram CallbackData factories — ensure pack/unpack stability."""
from __future__ import annotations
```

Then append the 7 tests above.

- [ ] **Step 3.2: Run tests — verify they fail**

```bash
uv run pytest tests/test_bot_callback_data.py -v -k "queue_"
```

Expected: 7 tests FAIL with `ImportError`.

- [ ] **Step 3.3: Add the CallbackData classes**

Append to `webhook/bot/callback_data.py`:

```python
class QueueModeToggle(CallbackData, prefix="q_mode"):
    """Enter or exit select mode from the /queue header."""
    action: str  # 'enter' | 'exit'


class QueueSelToggle(CallbackData, prefix="q_sel"):
    """Toggle selection of a single item (only valid in select mode)."""
    item_id: str


class QueueSelAll(CallbackData, prefix="q_all"):
    """Select every staging item across all pages."""
    pass


class QueueSelNone(CallbackData, prefix="q_none"):
    """Clear the current selection (keeps select mode active)."""
    pass


class QueueBulkPrompt(CallbackData, prefix="q_bulk"):
    """First tap on archive/discard — shows confirmation."""
    action: str  # 'archive' | 'discard'


class QueueBulkConfirm(CallbackData, prefix="q_bulkok"):
    """User confirmed — execute the action on current selection."""
    action: str  # 'archive' | 'discard'


class QueueBulkCancel(CallbackData, prefix="q_bulkno"):
    """Cancel confirmation, return to select-mode view."""
    pass
```

- [ ] **Step 3.4: Run tests — verify they pass**

```bash
uv run pytest tests/test_bot_callback_data.py -v -k "queue_"
```

Expected: 7 tests PASS.

- [ ] **Step 3.5: Commit**

```bash
git add webhook/bot/callback_data.py tests/test_bot_callback_data.py
git commit -m "feat(bot): add 7 CallbackData classes for /queue select mode"
```

---

## Task 4: `format_queue_page` select-mode rendering

**Files:**
- Modify: `webhook/query_handlers.py`
- Test: `tests/test_query_handlers.py` (append)

The existing signature `format_queue_page(page: int = 1)` grows `mode` and `selected` params. Default values keep every existing caller working. Normal mode gains an `☑️ Modo seleção` entry button above the items.

- [ ] **Step 4.1: Write the failing tests**

Append to `tests/test_query_handlers.py`:

```python
def test_queue_normal_mode_has_enter_select_button(fake_redis):
    """Normal /queue now has an '☑️ Modo seleção' button above items."""
    import json
    from webhook.query_handlers import format_queue_page
    fake_redis.set("platts:staging:x", json.dumps({
        "id": "x", "title": "T", "type": "news",
        "stagedAt": "2026-04-15T10:00:00Z",
    }))
    _, markup = format_queue_page(page=1)
    rows = markup["inline_keyboard"]
    # First row is the enter-select button
    assert rows[0][0]["text"] == "☑️ Modo seleção"
    assert rows[0][0]["callback_data"] == "q_mode:enter"
    # Item row follows
    assert rows[1][0]["text"].startswith("🗞️ T")


def test_queue_select_mode_header_shows_selection_count(fake_redis):
    import json
    from webhook.query_handlers import format_queue_page
    fake_redis.set("platts:staging:a", json.dumps({
        "id": "a", "title": "A", "type": "news",
        "stagedAt": "2026-04-15T10:00:00Z",
    }))
    fake_redis.set("platts:staging:b", json.dumps({
        "id": "b", "title": "B", "type": "news",
        "stagedAt": "2026-04-15T11:00:00Z",
    }))
    text, _ = format_queue_page(page=1, mode="select", selected={"a"})
    assert "1 selecionados de 2" in text


def test_queue_select_mode_items_render_checkboxes(fake_redis):
    import json
    from webhook.query_handlers import format_queue_page
    fake_redis.set("platts:staging:a", json.dumps({
        "id": "a", "title": "A", "type": "news",
        "stagedAt": "2026-04-15T10:00:00Z",
    }))
    fake_redis.set("platts:staging:b", json.dumps({
        "id": "b", "title": "B", "type": "news",
        "stagedAt": "2026-04-15T11:00:00Z",
    }))
    _, markup = format_queue_page(page=1, mode="select", selected={"a"})
    rows = markup["inline_keyboard"]
    # Find item rows (single-button rows with q_sel callback)
    item_rows = [r for r in rows if r[0]["callback_data"].startswith("q_sel:")]
    texts = [r[0]["text"] for r in item_rows]
    # 'a' selected gets ☑️, 'b' unselected gets ☐
    assert any(t.startswith("☑️") and "A" in t for t in texts)
    assert any(t.startswith("☐") and "B" in t for t in texts)


def test_queue_select_mode_has_action_rows(fake_redis):
    import json
    from webhook.query_handlers import format_queue_page
    fake_redis.set("platts:staging:a", json.dumps({
        "id": "a", "title": "A", "type": "news",
        "stagedAt": "2026-04-15T10:00:00Z",
    }))
    _, markup = format_queue_page(page=1, mode="select", selected={"a"})
    rows = markup["inline_keyboard"]
    all_texts = [b["text"] for row in rows for b in row]
    assert "✅ Todos" in all_texts
    assert "❌ Nenhum" in all_texts
    assert "📦 Arquivar 1" in all_texts
    assert "🗑️ Descartar 1" in all_texts
    assert "🔙 Sair" in all_texts


def test_queue_select_mode_counts_zero_when_no_selection(fake_redis):
    import json
    from webhook.query_handlers import format_queue_page
    fake_redis.set("platts:staging:a", json.dumps({
        "id": "a", "title": "A", "type": "news",
        "stagedAt": "2026-04-15T10:00:00Z",
    }))
    _, markup = format_queue_page(page=1, mode="select", selected=set())
    all_texts = [b["text"] for row in markup["inline_keyboard"] for b in row]
    assert "📦 Arquivar 0" in all_texts
    assert "🗑️ Descartar 0" in all_texts


def test_queue_select_mode_preserves_pagination(fake_redis):
    import json
    from webhook.query_handlers import format_queue_page
    for i in range(12):
        fake_redis.set(f"platts:staging:i{i:02d}", json.dumps({
            "id": f"i{i:02d}", "title": f"T{i:02d}", "type": "news",
            "stagedAt": f"2026-04-15T{i:02d}:00:00Z",
        }))
    _, markup = format_queue_page(page=1, mode="select", selected=set())
    rows = markup["inline_keyboard"]
    # Pagination row must still be present (last row)
    pag_texts = [b["text"] for b in rows[-1]]
    assert any("1/3" in t for t in pag_texts)


def test_queue_empty_keeps_old_output(fake_redis):
    """Empty staging still returns the old '(Nenhum item aguardando.)' string."""
    from webhook.query_handlers import format_queue_page
    text, markup = format_queue_page(page=1)
    assert text == "*🗂️ STAGING*\n\nNenhum item aguardando."
    assert markup is None
```

- [ ] **Step 4.2: Run tests — verify they fail**

```bash
uv run pytest tests/test_query_handlers.py -v
```

Expected: 6 of the 7 new tests FAIL (the `test_queue_empty_keeps_old_output` will pass — existing behavior). `test_queue_normal_mode_has_enter_select_button` fails because the button isn't there yet, and the select-mode tests fail because `format_queue_page` doesn't accept `mode`/`selected`.

- [ ] **Step 4.3: Extend `format_queue_page`**

Replace the contents of `webhook/query_handlers.py` from line 154 (the `def format_queue_page` block) to the end of the function (line 203). Paste this:

```python
def _format_queue_normal(total: int, time_info: str, page_items: list[dict],
                         total_pages: int, page: int) -> tuple[str, dict]:
    text = f"*🗂️ STAGING · {total} items{time_info}*"
    keyboard: list[list[dict]] = []
    keyboard.append([{
        "text": "☑️ Modo seleção",
        "callback_data": "q_mode:enter",
    }])
    for item in page_items:
        item_id = item.get("id") or ""
        staged = _format_staged_time(item.get("stagedAt", ""))
        time_tag = f" 🕐{staged}" if staged else ""
        keyboard.append([{
            "text": _queue_button_text(item) + time_tag,
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


def _format_queue_select(total: int, selected: set, time_info: str,
                         page_items: list[dict], total_pages: int,
                         page: int) -> tuple[str, dict]:
    selected_count = len(selected)
    text = f"*🗂️ STAGING · {selected_count} selecionados de {total}{time_info}*"
    keyboard: list[list[dict]] = []
    for item in page_items:
        item_id = item.get("id") or ""
        staged = _format_staged_time(item.get("stagedAt", ""))
        time_tag = f" 🕐{staged}" if staged else ""
        check = "☑️" if item_id in selected else "☐"
        label = f"{check} {_queue_button_text(item)}{time_tag}"
        keyboard.append([{
            "text": label,
            "callback_data": f"q_sel:{item_id}",
        }])
    keyboard.append([
        {"text": "✅ Todos", "callback_data": "q_all"},
        {"text": "❌ Nenhum", "callback_data": "q_none"},
    ])
    keyboard.append([
        {"text": f"📦 Arquivar {selected_count}", "callback_data": "q_bulk:archive"},
        {"text": f"🗑️ Descartar {selected_count}", "callback_data": "q_bulk:discard"},
    ])
    keyboard.append([{"text": "🔙 Sair", "callback_data": "q_mode:exit"}])
    if total_pages > 1:
        row: list[dict] = []
        if page > 1:
            row.append({"text": "⬅ anterior", "callback_data": f"queue_page:{page - 1}"})
        row.append({"text": f"{page}/{total_pages}", "callback_data": "noop"})
        if page < total_pages:
            row.append({"text": "próximo ➡", "callback_data": f"queue_page:{page + 1}"})
        keyboard.append(row)
    return text, {"inline_keyboard": keyboard}


def format_queue_page(
    page: int = 1,
    mode: str = "normal",
    selected: Optional[set] = None,
) -> tuple[str, Optional[dict]]:
    """Return (text, reply_markup) for /queue at given 1-indexed page.

    mode='normal' (default) renders each item as a single button opening
    the curation card, plus an '☑️ Modo seleção' entry button at the top.

    mode='select' renders each item as a checkbox toggle, plus action
    rows (✅ Todos / ❌ Nenhum / 📦 Arquivar N / 🗑️ Descartar N / 🔙 Sair).
    'selected' must be the set of currently selected item ids.
    """
    if selected is None:
        selected = set()
    items = redis_queries.list_staging(limit=200)
    total = len(items)
    if total == 0:
        return "*🗂️ STAGING*\n\nNenhum item aguardando.", None

    total_pages = (total + _QUEUE_PAGE_SIZE - 1) // _QUEUE_PAGE_SIZE
    page = max(1, min(page, total_pages))
    start = (page - 1) * _QUEUE_PAGE_SIZE
    end = start + _QUEUE_PAGE_SIZE
    page_items = items[start:end]

    staged_times = [i.get("stagedAt", "") for i in items if i.get("stagedAt")]
    if staged_times:
        oldest = _format_staged_time(min(staged_times))
        newest = _format_staged_time(max(staged_times))
        time_info = (
            f" · coletados {oldest}–{newest} BRT"
            if oldest != newest
            else f" · coletado {newest} BRT"
        )
    else:
        time_info = ""

    if mode == "select":
        return _format_queue_select(total, selected, time_info, page_items, total_pages, page)
    return _format_queue_normal(total, time_info, page_items, total_pages, page)
```

- [ ] **Step 4.4: Run tests — verify they pass**

```bash
uv run pytest tests/test_query_handlers.py -v
```

Expected: all 26 tests PASS (19 old + 7 new). Pay special attention that the original tests for `test_queue_single_page_titles_in_buttons`, `test_queue_paginated`, `test_queue_truncates_long_title_in_button`, `test_queue_escapes_markdown_in_button_text` still pass — the item-row structure is unchanged in normal mode; only the entry button was added above them.

Before re-running, 4 existing tests need their indices shifted by +1 because normal-mode now has an entry button at row 0:

**1) `test_queue_single_page_titles_in_buttons`** — change `len(buttons) == 3` to `== 4`, and shift `buttons[0]` → `buttons[1]`:

```python
assert len(buttons) == 4
assert buttons[1][0]["text"].startswith("🗞️ Title 0")
assert "🕐" in buttons[1][0]["text"]
assert buttons[1][0]["callback_data"] == "queue_open:item0"
```

**2) `test_queue_paginated`** — change `len(markup_p1["inline_keyboard"]) == 6` to `== 7`, and shift first item from `[0]` to `[1]`:

```python
assert len(markup_p1["inline_keyboard"]) == 7
# Item buttons têm o título
assert markup_p1["inline_keyboard"][1][0]["text"].startswith("🗞️ Title 11")
```

The `pag_texts = [b["text"] for b in markup_p1["inline_keyboard"][-1]]` line is unchanged — the pagination row is still `[-1]`.

**3) `test_queue_truncates_long_title_in_button`** — shift `[0]` to `[1]`:

```python
btn_text = markup["inline_keyboard"][1][0]["text"]
```

**4) `test_queue_escapes_markdown_in_button_text`** — shift `[0]` to `[1]`:

```python
btn_text = markup["inline_keyboard"][1][0]["text"]
```

Make those 4 edits to `tests/test_query_handlers.py` before re-running. After edits, all 26 tests pass.

- [ ] **Step 4.5: Commit**

```bash
git add webhook/query_handlers.py tests/test_query_handlers.py
git commit -m "feat(queue): render select-mode layout in format_queue_page"
```

---

## Task 5: Mode toggle handler + update `on_queue_page`

**Files:**
- Modify: `webhook/bot/routers/callbacks_queue.py`
- Test: `tests/test_callbacks_queue.py` (append)

The existing `on_queue_page` now reads the current mode and passes it to `format_queue_page`. A new `on_queue_mode` handles enter/exit.

- [ ] **Step 5.1: Write the failing tests**

Append to `tests/test_callbacks_queue.py`:

```python
@pytest.mark.asyncio
async def test_on_queue_mode_enter_activates_select_mode(mock_callback_query, mocker):
    from bot.callback_data import QueueModeToggle
    from bot.routers.callbacks_queue import on_queue_mode

    enter_mock = mocker.patch("webhook.queue_selection.enter_mode")
    mocker.patch(
        "bot.routers.callbacks_queue.query_handlers.format_queue_page",
        return_value=("body", {"inline_keyboard": []}),
    )
    bot = AsyncMock()
    bot.edit_message_text = AsyncMock()
    mocker.patch("bot.routers.callbacks_queue.get_bot", return_value=bot)

    query = mock_callback_query(chat_id=42, message_id=99)
    await on_queue_mode(query, QueueModeToggle(action="enter"))

    enter_mock.assert_called_once_with(42)
    bot.edit_message_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_queue_mode_exit_clears_state(mock_callback_query, mocker):
    from bot.callback_data import QueueModeToggle
    from bot.routers.callbacks_queue import on_queue_mode

    exit_mock = mocker.patch("webhook.queue_selection.exit_mode")
    mocker.patch(
        "bot.routers.callbacks_queue.query_handlers.format_queue_page",
        return_value=("body", {"inline_keyboard": []}),
    )
    bot = AsyncMock()
    mocker.patch("bot.routers.callbacks_queue.get_bot", return_value=bot)

    query = mock_callback_query(chat_id=42)
    await on_queue_mode(query, QueueModeToggle(action="exit"))

    exit_mock.assert_called_once_with(42)


@pytest.mark.asyncio
async def test_on_queue_page_uses_select_mode_when_active(mock_callback_query, mocker):
    from bot.callback_data import QueuePage
    from bot.routers.callbacks_queue import on_queue_page

    mocker.patch("webhook.queue_selection.is_select_mode", return_value=True)
    mocker.patch("webhook.queue_selection.get_selection", return_value={"a"})
    format_mock = mocker.patch(
        "bot.routers.callbacks_queue.query_handlers.format_queue_page",
        return_value=("body", {"inline_keyboard": []}),
    )
    bot = AsyncMock()
    mocker.patch("bot.routers.callbacks_queue.get_bot", return_value=bot)

    await on_queue_page(mock_callback_query(chat_id=42), QueuePage(page=2))

    kwargs = format_mock.call_args.kwargs
    assert kwargs["mode"] == "select"
    assert kwargs["selected"] == {"a"}


@pytest.mark.asyncio
async def test_on_queue_page_uses_normal_mode_when_inactive(mock_callback_query, mocker):
    from bot.callback_data import QueuePage
    from bot.routers.callbacks_queue import on_queue_page

    mocker.patch("webhook.queue_selection.is_select_mode", return_value=False)
    format_mock = mocker.patch(
        "bot.routers.callbacks_queue.query_handlers.format_queue_page",
        return_value=("body", {"inline_keyboard": []}),
    )
    bot = AsyncMock()
    mocker.patch("bot.routers.callbacks_queue.get_bot", return_value=bot)

    await on_queue_page(mock_callback_query(chat_id=42), QueuePage(page=1))

    kwargs = format_mock.call_args.kwargs
    assert kwargs["mode"] == "normal"
```

- [ ] **Step 5.2: Run tests — verify they fail**

```bash
uv run pytest tests/test_callbacks_queue.py -v
```

Expected: 4 new tests FAIL (`on_queue_mode` not defined; `on_queue_page` does not pass mode kwarg).

- [ ] **Step 5.3: Update `callbacks_queue.py`**

Replace the current content of `webhook/bot/routers/callbacks_queue.py` with:

```python
"""Callback handlers for queue navigation and bulk actions."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from aiogram import Router
from aiogram.types import CallbackQuery

from bot.callback_data import (
    QueuePage, QueueOpen,
    QueueModeToggle, QueueSelToggle, QueueSelAll, QueueSelNone,
    QueueBulkPrompt, QueueBulkConfirm, QueueBulkCancel,
)
from bot.config import get_bot
from bot.middlewares.auth import RoleMiddleware
import query_handlers
import redis_queries
from execution.curation import redis_client as curation_redis
from execution.curation import telegram_poster
from webhook import queue_selection

logger = logging.getLogger(__name__)

callbacks_queue_router = Router(name="callbacks_queue")
callbacks_queue_router.callback_query.middleware(RoleMiddleware(allowed_roles={"admin"}))


def _current_mode(chat_id: int) -> tuple[str, set[str]]:
    if queue_selection.is_select_mode(chat_id):
        return "select", queue_selection.get_selection(chat_id)
    return "normal", set()


async def _rerender(query: CallbackQuery, page: int = 1) -> None:
    """Re-render the /queue message in place, honoring current mode."""
    chat_id = query.message.chat.id
    mode, selected = _current_mode(chat_id)
    try:
        body, markup = query_handlers.format_queue_page(
            page=page, mode=mode, selected=selected,
        )
    except Exception as exc:
        logger.error(f"queue rerender error: {exc}")
        return
    await get_bot().edit_message_text(
        body,
        chat_id=chat_id,
        message_id=query.message.message_id,
        reply_markup=markup,
    )


# ── Queue navigation ──

@callbacks_queue_router.callback_query(QueuePage.filter())
async def on_queue_page(query: CallbackQuery, callback_data: QueuePage):
    await query.answer("")
    await _rerender(query, page=callback_data.page)


@callbacks_queue_router.callback_query(QueueOpen.filter())
async def on_queue_open(query: CallbackQuery, callback_data: QueueOpen):
    chat_id = query.message.chat.id
    try:
        item = curation_redis.get_staging(callback_data.item_id)
    except Exception as exc:
        logger.error(f"queue_open redis error: {exc}")
        await query.answer("⚠️ Redis indisponível")
        return
    if item is None:
        await query.answer("⚠️ Item expirou")
        return
    await query.answer("")
    preview_base_url = os.getenv("TELEGRAM_WEBHOOK_URL", "").rstrip("/")
    try:
        await asyncio.to_thread(
            telegram_poster.post_for_curation, chat_id, item, preview_base_url,
        )
    except Exception as exc:
        logger.error(f"queue_open post error: {exc}")
        await query.message.answer("❌ Erro ao abrir card.")


# ── Select mode enter/exit ──

@callbacks_queue_router.callback_query(QueueModeToggle.filter())
async def on_queue_mode(query: CallbackQuery, callback_data: QueueModeToggle):
    chat_id = query.message.chat.id
    if callback_data.action == "enter":
        queue_selection.enter_mode(chat_id)
        await query.answer("Modo seleção ativado")
    else:
        queue_selection.exit_mode(chat_id)
        await query.answer("Saiu do modo seleção")
    await _rerender(query, page=1)
```

- [ ] **Step 5.4: Run tests — verify they pass**

```bash
uv run pytest tests/test_callbacks_queue.py -v
```

Expected: all tests PASS (4 existing + 4 new = 8). The existing `test_on_queue_page_format_error_returns_silently` expects that on `format_queue_page` error nothing is sent — the `_rerender` helper logs and returns, so it still passes.

- [ ] **Step 5.5: Commit**

```bash
git add webhook/bot/routers/callbacks_queue.py tests/test_callbacks_queue.py
git commit -m "feat(queue): add mode toggle handler + mode-aware page rerender"
```

---

## Task 6: Item selection handlers (toggle / all / none)

**Files:**
- Modify: `webhook/bot/routers/callbacks_queue.py` (append handlers)
- Test: `tests/test_callbacks_queue.py` (append)

- [ ] **Step 6.1: Write the failing tests**

Append to `tests/test_callbacks_queue.py`:

```python
@pytest.mark.asyncio
async def test_on_queue_sel_toggle_adds_and_rerenders(mock_callback_query, mocker):
    from bot.callback_data import QueueSelToggle
    from bot.routers.callbacks_queue import on_queue_sel_toggle

    toggle_mock = mocker.patch("webhook.queue_selection.toggle", return_value=True)
    mocker.patch("webhook.queue_selection.is_select_mode", return_value=True)
    mocker.patch("webhook.queue_selection.get_selection", return_value={"abc"})
    mocker.patch(
        "bot.routers.callbacks_queue.query_handlers.format_queue_page",
        return_value=("body", {"inline_keyboard": []}),
    )
    bot = AsyncMock()
    mocker.patch("bot.routers.callbacks_queue.get_bot", return_value=bot)

    await on_queue_sel_toggle(
        mock_callback_query(chat_id=42), QueueSelToggle(item_id="abc"),
    )

    toggle_mock.assert_called_once_with(42, "abc")
    bot.edit_message_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_queue_sel_toggle_not_in_select_mode_toasts(mock_callback_query, mocker):
    from bot.callback_data import QueueSelToggle
    from bot.routers.callbacks_queue import on_queue_sel_toggle

    mocker.patch("webhook.queue_selection.is_select_mode", return_value=False)

    query = mock_callback_query(chat_id=42)
    await on_queue_sel_toggle(query, QueueSelToggle(item_id="abc"))

    query.answer.assert_awaited_with("Seleção expirou, entre no modo novamente")


@pytest.mark.asyncio
async def test_on_queue_sel_all_selects_every_staging_id(mock_callback_query, mocker):
    from bot.callback_data import QueueSelAll
    from bot.routers.callbacks_queue import on_queue_sel_all

    mocker.patch("webhook.queue_selection.is_select_mode", return_value=True)
    mocker.patch("webhook.queue_selection.get_selection", return_value={"a", "b"})
    mocker.patch(
        "redis_queries.list_staging",
        return_value=[{"id": "a"}, {"id": "b"}],
    )
    select_all_mock = mocker.patch("webhook.queue_selection.select_all")
    mocker.patch(
        "bot.routers.callbacks_queue.query_handlers.format_queue_page",
        return_value=("body", {"inline_keyboard": []}),
    )
    bot = AsyncMock()
    mocker.patch("bot.routers.callbacks_queue.get_bot", return_value=bot)

    await on_queue_sel_all(mock_callback_query(chat_id=42), QueueSelAll())

    select_all_mock.assert_called_once()
    args = select_all_mock.call_args.args
    assert args[0] == 42
    assert sorted(args[1]) == ["a", "b"]


@pytest.mark.asyncio
async def test_on_queue_sel_none_clears_selection(mock_callback_query, mocker):
    from bot.callback_data import QueueSelNone
    from bot.routers.callbacks_queue import on_queue_sel_none

    mocker.patch("webhook.queue_selection.is_select_mode", return_value=True)
    mocker.patch("webhook.queue_selection.get_selection", return_value=set())
    clear_mock = mocker.patch("webhook.queue_selection.clear")
    mocker.patch(
        "bot.routers.callbacks_queue.query_handlers.format_queue_page",
        return_value=("body", {"inline_keyboard": []}),
    )
    bot = AsyncMock()
    mocker.patch("bot.routers.callbacks_queue.get_bot", return_value=bot)

    await on_queue_sel_none(mock_callback_query(chat_id=42), QueueSelNone())

    clear_mock.assert_called_once_with(42)
```

- [ ] **Step 6.2: Run tests — verify they fail**

```bash
uv run pytest tests/test_callbacks_queue.py -v -k "sel_"
```

Expected: 4 tests FAIL (handlers not defined).

- [ ] **Step 6.3: Append handlers**

Append to `webhook/bot/routers/callbacks_queue.py`:

```python
# ── Item selection ──

@callbacks_queue_router.callback_query(QueueSelToggle.filter())
async def on_queue_sel_toggle(query: CallbackQuery, callback_data: QueueSelToggle):
    chat_id = query.message.chat.id
    if not queue_selection.is_select_mode(chat_id):
        await query.answer("Seleção expirou, entre no modo novamente")
        return
    try:
        queue_selection.toggle(chat_id, callback_data.item_id)
    except Exception as exc:
        logger.error(f"queue_sel_toggle redis error: {exc}")
        await query.answer("⚠️ Redis indisponível")
        return
    await query.answer("")
    await _rerender(query, page=1)


@callbacks_queue_router.callback_query(QueueSelAll.filter())
async def on_queue_sel_all(query: CallbackQuery, callback_data: QueueSelAll):
    chat_id = query.message.chat.id
    if not queue_selection.is_select_mode(chat_id):
        await query.answer("Seleção expirou, entre no modo novamente")
        return
    try:
        items = redis_queries.list_staging(limit=200)
    except Exception as exc:
        logger.error(f"queue_sel_all redis error: {exc}")
        await query.answer("⚠️ Redis indisponível")
        return
    ids = [i["id"] for i in items if i.get("id")]
    queue_selection.select_all(chat_id, ids)
    await query.answer(f"{len(ids)} selecionados")
    await _rerender(query, page=1)


@callbacks_queue_router.callback_query(QueueSelNone.filter())
async def on_queue_sel_none(query: CallbackQuery, callback_data: QueueSelNone):
    chat_id = query.message.chat.id
    if not queue_selection.is_select_mode(chat_id):
        await query.answer("Seleção expirou, entre no modo novamente")
        return
    queue_selection.clear(chat_id)
    await query.answer("Seleção limpa")
    await _rerender(query, page=1)
```

- [ ] **Step 6.4: Run tests — verify they pass**

```bash
uv run pytest tests/test_callbacks_queue.py -v -k "sel_"
```

Expected: all 4 tests PASS.

- [ ] **Step 6.5: Commit**

```bash
git add webhook/bot/routers/callbacks_queue.py tests/test_callbacks_queue.py
git commit -m "feat(queue): add item toggle, select-all, clear handlers"
```

---

## Task 7: Bulk action flow (prompt + confirm + cancel)

**Files:**
- Modify: `webhook/bot/routers/callbacks_queue.py` (append handlers)
- Test: `tests/test_callbacks_queue.py` (append)

This is the final task. The `QueueBulkPrompt` handler shows a confirmation dialog. `QueueBulkConfirm` executes and auto-exits select mode. `QueueBulkCancel` returns to the select-mode view.

- [ ] **Step 7.1: Write the failing tests**

Append to `tests/test_callbacks_queue.py`:

```python
@pytest.mark.asyncio
async def test_on_queue_bulk_prompt_empty_selection_toasts(mock_callback_query, mocker):
    from bot.callback_data import QueueBulkPrompt
    from bot.routers.callbacks_queue import on_queue_bulk_prompt

    mocker.patch("webhook.queue_selection.is_select_mode", return_value=True)
    mocker.patch("webhook.queue_selection.get_selection", return_value=set())

    query = mock_callback_query(chat_id=42)
    await on_queue_bulk_prompt(query, QueueBulkPrompt(action="archive"))

    query.answer.assert_awaited_with("Nada selecionado")


@pytest.mark.asyncio
async def test_on_queue_bulk_prompt_archive_shows_confirmation(mock_callback_query, mocker):
    from bot.callback_data import QueueBulkPrompt
    from bot.routers.callbacks_queue import on_queue_bulk_prompt

    mocker.patch("webhook.queue_selection.is_select_mode", return_value=True)
    mocker.patch("webhook.queue_selection.get_selection", return_value={"a", "b", "c"})
    bot = AsyncMock()
    bot.edit_message_text = AsyncMock()
    mocker.patch("bot.routers.callbacks_queue.get_bot", return_value=bot)

    query = mock_callback_query(chat_id=42, message_id=99)
    await on_queue_bulk_prompt(query, QueueBulkPrompt(action="archive"))

    bot.edit_message_text.assert_awaited_once()
    call = bot.edit_message_text.await_args
    assert "Arquivar 3 items?" in call.args[0]
    markup = call.kwargs["reply_markup"]
    texts = [b["text"] for row in markup["inline_keyboard"] for b in row]
    assert "✅ Sim" in texts
    assert "❌ Cancelar" in texts


@pytest.mark.asyncio
async def test_on_queue_bulk_prompt_discard_shows_confirmation(mock_callback_query, mocker):
    from bot.callback_data import QueueBulkPrompt
    from bot.routers.callbacks_queue import on_queue_bulk_prompt

    mocker.patch("webhook.queue_selection.is_select_mode", return_value=True)
    mocker.patch("webhook.queue_selection.get_selection", return_value={"a"})
    bot = AsyncMock()
    bot.edit_message_text = AsyncMock()
    mocker.patch("bot.routers.callbacks_queue.get_bot", return_value=bot)

    query = mock_callback_query(chat_id=42)
    await on_queue_bulk_prompt(query, QueueBulkPrompt(action="discard"))

    call = bot.edit_message_text.await_args
    assert "Descartar 1 items?" in call.args[0]


@pytest.mark.asyncio
async def test_on_queue_bulk_confirm_archive_executes_then_exits(mock_callback_query, mocker):
    from bot.callback_data import QueueBulkConfirm
    from bot.routers.callbacks_queue import on_queue_bulk_confirm

    mocker.patch("webhook.queue_selection.is_select_mode", return_value=True)
    mocker.patch("webhook.queue_selection.get_selection", return_value={"a", "b"})
    exit_mock = mocker.patch("webhook.queue_selection.exit_mode")
    to_thread = mocker.patch(
        "asyncio.to_thread",
        new=AsyncMock(return_value={"archived": ["a", "b"], "failed": []}),
    )
    mocker.patch(
        "bot.routers.callbacks_queue.query_handlers.format_queue_page",
        return_value=("body", {"inline_keyboard": []}),
    )
    bot = AsyncMock()
    mocker.patch("bot.routers.callbacks_queue.get_bot", return_value=bot)

    query = mock_callback_query(chat_id=42)
    await on_queue_bulk_confirm(query, QueueBulkConfirm(action="archive"))

    to_thread.assert_awaited_once()
    query.answer.assert_awaited_with("✅ 2 arquivados")
    exit_mock.assert_called_once_with(42)


@pytest.mark.asyncio
async def test_on_queue_bulk_confirm_archive_partial_reports_both_counts(mock_callback_query, mocker):
    from bot.callback_data import QueueBulkConfirm
    from bot.routers.callbacks_queue import on_queue_bulk_confirm

    mocker.patch("webhook.queue_selection.is_select_mode", return_value=True)
    mocker.patch("webhook.queue_selection.get_selection", return_value={"a", "b", "c"})
    mocker.patch("webhook.queue_selection.exit_mode")
    mocker.patch(
        "asyncio.to_thread",
        new=AsyncMock(return_value={"archived": ["a", "b"], "failed": ["c"]}),
    )
    mocker.patch(
        "bot.routers.callbacks_queue.query_handlers.format_queue_page",
        return_value=("body", {"inline_keyboard": []}),
    )
    mocker.patch("bot.routers.callbacks_queue.get_bot", return_value=AsyncMock())

    query = mock_callback_query(chat_id=42)
    await on_queue_bulk_confirm(query, QueueBulkConfirm(action="archive"))

    query.answer.assert_awaited_with("✅ 2 arquivados, 1 falhou (expirado ou já removido)")


@pytest.mark.asyncio
async def test_on_queue_bulk_confirm_discard_executes(mock_callback_query, mocker):
    from bot.callback_data import QueueBulkConfirm
    from bot.routers.callbacks_queue import on_queue_bulk_confirm

    mocker.patch("webhook.queue_selection.is_select_mode", return_value=True)
    mocker.patch("webhook.queue_selection.get_selection", return_value={"a", "b"})
    mocker.patch("webhook.queue_selection.exit_mode")
    to_thread = mocker.patch("asyncio.to_thread", new=AsyncMock(return_value=2))
    mocker.patch(
        "bot.routers.callbacks_queue.query_handlers.format_queue_page",
        return_value=("body", {"inline_keyboard": []}),
    )
    mocker.patch("bot.routers.callbacks_queue.get_bot", return_value=AsyncMock())

    query = mock_callback_query(chat_id=42)
    await on_queue_bulk_confirm(query, QueueBulkConfirm(action="discard"))

    to_thread.assert_awaited_once()
    query.answer.assert_awaited_with("✅ 2 descartados")


@pytest.mark.asyncio
async def test_on_queue_bulk_confirm_empty_selection_toasts(mock_callback_query, mocker):
    from bot.callback_data import QueueBulkConfirm
    from bot.routers.callbacks_queue import on_queue_bulk_confirm

    mocker.patch("webhook.queue_selection.is_select_mode", return_value=True)
    mocker.patch("webhook.queue_selection.get_selection", return_value=set())

    query = mock_callback_query(chat_id=42)
    await on_queue_bulk_confirm(query, QueueBulkConfirm(action="archive"))

    query.answer.assert_awaited_with("Seleção expirou, entre no modo novamente")


@pytest.mark.asyncio
async def test_on_queue_bulk_cancel_rerenders_select_mode(mock_callback_query, mocker):
    from bot.callback_data import QueueBulkCancel
    from bot.routers.callbacks_queue import on_queue_bulk_cancel

    mocker.patch("webhook.queue_selection.is_select_mode", return_value=True)
    mocker.patch("webhook.queue_selection.get_selection", return_value={"a"})
    mocker.patch(
        "bot.routers.callbacks_queue.query_handlers.format_queue_page",
        return_value=("body", {"inline_keyboard": []}),
    )
    bot = AsyncMock()
    bot.edit_message_text = AsyncMock()
    mocker.patch("bot.routers.callbacks_queue.get_bot", return_value=bot)

    query = mock_callback_query(chat_id=42)
    await on_queue_bulk_cancel(query, QueueBulkCancel())

    query.answer.assert_awaited_with("Cancelado")
    bot.edit_message_text.assert_awaited_once()
```

- [ ] **Step 7.2: Run tests — verify they fail**

```bash
uv run pytest tests/test_callbacks_queue.py -v -k "bulk"
```

Expected: 8 tests FAIL (handlers not defined).

- [ ] **Step 7.3: Append handlers**

Append to `webhook/bot/routers/callbacks_queue.py`:

```python
# ── Bulk action flow ──

_BULK_ACTION_VERBS = {
    "archive": ("Arquivar", "arquivados"),
    "discard": ("Descartar", "descartados"),
}


def _confirm_markup(action: str) -> dict:
    return {
        "inline_keyboard": [[
            {
                "text": "✅ Sim",
                "callback_data": QueueBulkConfirm(action=action).pack(),
            },
            {
                "text": "❌ Cancelar",
                "callback_data": QueueBulkCancel().pack(),
            },
        ]]
    }


@callbacks_queue_router.callback_query(QueueBulkPrompt.filter())
async def on_queue_bulk_prompt(query: CallbackQuery, callback_data: QueueBulkPrompt):
    chat_id = query.message.chat.id
    if not queue_selection.is_select_mode(chat_id):
        await query.answer("Seleção expirou, entre no modo novamente")
        return
    selected = queue_selection.get_selection(chat_id)
    if not selected:
        await query.answer("Nada selecionado")
        return
    verb_title, _ = _BULK_ACTION_VERBS[callback_data.action]
    prompt = f"{verb_title} {len(selected)} items?"
    await query.answer("")
    await get_bot().edit_message_text(
        prompt,
        chat_id=chat_id,
        message_id=query.message.message_id,
        reply_markup=_confirm_markup(callback_data.action),
    )


@callbacks_queue_router.callback_query(QueueBulkConfirm.filter())
async def on_queue_bulk_confirm(query: CallbackQuery, callback_data: QueueBulkConfirm):
    chat_id = query.message.chat.id
    if not queue_selection.is_select_mode(chat_id):
        await query.answer("Seleção expirou, entre no modo novamente")
        return
    selected = queue_selection.get_selection(chat_id)
    if not selected:
        await query.answer("Seleção expirou, entre no modo novamente")
        return
    ids = sorted(selected)  # deterministic order for bulk op

    if callback_data.action == "archive":
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            result = await asyncio.to_thread(
                curation_redis.bulk_archive, ids, date, chat_id,
            )
        except Exception as exc:
            logger.error(f"bulk_archive failed: {exc}")
            await query.answer("⚠️ Erro ao arquivar")
            return
        ok = len(result["archived"])
        bad = len(result["failed"])
        if ok and bad:
            toast = f"✅ {ok} arquivados, {bad} falhou (expirado ou já removido)"
        elif ok:
            toast = f"✅ {ok} arquivados"
        else:
            toast = "⚠️ Nenhum item arquivado (todos expiraram ou foram removidos)"
    else:  # discard
        try:
            deleted = await asyncio.to_thread(curation_redis.bulk_discard, ids)
        except Exception as exc:
            logger.error(f"bulk_discard failed: {exc}")
            await query.answer("⚠️ Erro ao descartar")
            return
        toast = f"✅ {int(deleted)} descartados"

    queue_selection.exit_mode(chat_id)
    await query.answer(toast)
    await _rerender(query, page=1)


@callbacks_queue_router.callback_query(QueueBulkCancel.filter())
async def on_queue_bulk_cancel(query: CallbackQuery, callback_data: QueueBulkCancel):
    await query.answer("Cancelado")
    await _rerender(query, page=1)
```

- [ ] **Step 7.4: Run tests — verify they pass**

```bash
uv run pytest tests/test_callbacks_queue.py -v
```

Expected: all tests PASS (4 original + 4 mode + 4 selection + 8 bulk = 20).

- [ ] **Step 7.5: Full suite regression**

```bash
uv run pytest --no-header -q
```

Expected: 614 + 34 new = 648 tests pass (or higher if the other instance added more). No pre-existing failures introduced.

- [ ] **Step 7.6: Commit**

```bash
git add webhook/bot/routers/callbacks_queue.py tests/test_callbacks_queue.py
git commit -m "feat(queue): add bulk prompt/confirm/cancel handlers"
```

---

## Plan Self-Review Notes

**Spec coverage:**
- P1 archive+discard ✓ (Task 2, Task 7)
- P2 selection persists across pages ✓ (Task 1's Redis set scope, Task 4's render reads full selection regardless of page)
- P3 confirmation step ✓ (Task 7's `on_queue_bulk_prompt`)
- P4 separate archive/discard buttons ✓ (Task 4's select-mode layout)
- Auto-exit after success ✓ (Task 7's `on_queue_bulk_confirm` calls `exit_mode`)
- Empty-selection protection ✓ (Task 7 toast path; Task 4 renders `📦 Arquivar 0`)
- TTL expired mid-flow ✓ (every handler guards with `is_select_mode`)
- Select-all spans all pages ✓ (Task 6's `on_queue_sel_all` calls `redis_queries.list_staging(limit=200)`)

**Placeholder scan:** none.

**Type consistency:**
- `bulk_archive` returns `dict` with keys `"archived"` / `"failed"` — used consistently in Task 7 handler
- `bulk_discard` returns `int` — used in Task 7 handler as deleted count
- `format_queue_page(page, mode, selected)` signature matches every caller in Tasks 5, 6, 7
- `queue_selection` public API (`enter_mode`, `exit_mode`, `is_select_mode`, `get_selection`, `toggle`, `select_all`, `clear`) used consistently

**Ambiguity notes:**
- Task 4, Step 4.4 explicitly calls out updating the existing pagination test because the entry-button shifts item indices by +1. Addressed.
- `asyncio.to_thread` patching in Task 7 tests covers both archive (returns dict) and discard (returns int) via the `return_value` swap.
