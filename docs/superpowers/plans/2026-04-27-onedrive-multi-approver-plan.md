# OneDrive Multi-Approver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow up to 5 trusted Telegram users (initially admin + 1 colleague) to receive and act on OneDrive PDF approval cards via Telegram, with race-safe first-click claim semantics and cascade card edits to the non-clicker recipients.

**Architecture:** Capability-based authorization (`is_onedrive_approver(chat_id)`) orthogonal to the existing role enum. New env var `ONEDRIVE_APPROVER_IDS` (CSV). Atomic Redis claim lock (`approval:{uuid}:claimed_by`) ensures first click wins; cascade edits (admin/approver A clicked → all other recipients' cards edited in parallel via `asyncio.gather`) keep state consistent. Backward-compatible: empty env var preserves v1 single-admin behavior exactly.

**Tech Stack:** Python 3.11, aiogram v3, Redis (via `redis.asyncio`), Supabase (no schema changes), pytest + fakeredis + AsyncMock for tests.

**Spec:** `docs/superpowers/specs/2026-04-27-onedrive-multi-approver-design.md`

---

## File Structure

| File | Type | Purpose |
|---|---|---|
| `webhook/bot/users.py` | modify | + `get_onedrive_approver_ids()`, `is_onedrive_approver()`, `format_user_label()`. `get_user_role()` unchanged. |
| `webhook/onedrive_pipeline.py` | modify | `process_notification` fan-out across `[admin] + approvers`, persists `recipients` array in approval state. |
| `webhook/bot/routers/callbacks_onedrive.py` | modify | + `_claim()`, `_edit_others()` helpers. Replace `RoleMiddleware` with per-handler `F.func` capability filter. Modify `on_approve`, `on_confirm`, `on_discard` to claim + cascade. |
| `webhook/bot/routers/onboarding.py` | modify | `/start` early return for approver-only chats (no `pending` user record created). |
| `.env.example` | modify | Document `ONEDRIVE_APPROVER_IDS`. |
| `tests/test_users_onedrive_approver.py` | create | Unit tests for new helpers. |
| `tests/test_onedrive_pipeline.py` | modify | Add fan-out, recipients persistence, partial-failure tests. |
| `tests/test_onedrive_callbacks.py` | modify | Add `_claim`, `_edit_others`, on_approve/on_confirm/on_discard cascade + race tests. |
| `tests/test_onboarding_approver.py` | create | Test `/start` early return path. |

**Total estimated diff:** ~150 lines new Python + ~120 lines new tests across the 9 files above.

---

## Pre-flight: Confirm baseline tests pass

- [ ] **Step 0.1: Run existing OneDrive test suite to establish green baseline**

```bash
cd /Users/bigode/Dev/agentics_workflows
pytest tests/test_onedrive_pipeline.py tests/test_onedrive_callbacks.py tests/test_onedrive_route.py tests/test_onedrive_resubscribe.py -v
```

Expected: all PASS. If any FAIL, stop and investigate before proceeding — you don't want to mix pre-existing breakage with new work.

---

## Task 1: Capability helpers in `users.py`

**Files:**
- Create: `tests/test_users_onedrive_approver.py`
- Modify: `webhook/bot/users.py` (add new functions only — no changes to existing functions)

- [ ] **Step 1.1: Write the failing test file**

Create `tests/test_users_onedrive_approver.py`:

```python
"""Unit tests for OneDrive approver capability helpers in webhook/bot/users.py."""
from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest


# ── get_onedrive_approver_ids ──

def test_get_approver_ids_empty_env(monkeypatch):
    monkeypatch.delenv("ONEDRIVE_APPROVER_IDS", raising=False)
    from bot.users import get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()
    assert get_onedrive_approver_ids() == []


def test_get_approver_ids_unset_env_returns_empty(monkeypatch):
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "")
    from bot.users import get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()
    assert get_onedrive_approver_ids() == []


def test_get_approver_ids_single(monkeypatch):
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "123")
    from bot.users import get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()
    assert get_onedrive_approver_ids() == [123]


def test_get_approver_ids_csv(monkeypatch):
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "123,456,789")
    from bot.users import get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()
    assert get_onedrive_approver_ids() == [123, 456, 789]


def test_get_approver_ids_with_whitespace(monkeypatch):
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", " 123 , 456 ,789 ")
    from bot.users import get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()
    assert get_onedrive_approver_ids() == [123, 456, 789]


def test_get_approver_ids_skips_malformed(monkeypatch, caplog):
    import logging
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "123,abc,456,,xyz")
    from bot.users import get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()
    with caplog.at_level(logging.WARNING):
        result = get_onedrive_approver_ids()
    assert result == [123, 456]
    # At least one warning logged about the malformed values
    assert any("approver" in r.message.lower() for r in caplog.records)


def test_get_approver_ids_caches_result(monkeypatch):
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "111")
    from bot.users import get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()
    first = get_onedrive_approver_ids()
    # Mutate env after first call — cache should win
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "222")
    second = get_onedrive_approver_ids()
    assert first == second == [111]


# ── is_onedrive_approver ──

def test_is_onedrive_approver_chat_in_env(monkeypatch):
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "456")
    from bot.users import is_onedrive_approver, get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()
    assert is_onedrive_approver(456) is True


def test_is_onedrive_approver_chat_not_in_env(monkeypatch):
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "456")
    from bot.users import is_onedrive_approver, get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()
    assert is_onedrive_approver(999) is False


def test_is_onedrive_approver_admin_implicit(monkeypatch):
    """Admin always passes regardless of env var."""
    monkeypatch.delenv("ONEDRIVE_APPROVER_IDS", raising=False)
    from bot.users import is_onedrive_approver, get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()
    with patch("bot.users.is_admin", return_value=True):
        assert is_onedrive_approver(123) is True


def test_is_onedrive_approver_subscriber_in_env(monkeypatch):
    """Subscriber added to env still gets capability — orthogonal to role."""
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "555")
    from bot.users import is_onedrive_approver, get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()
    with patch("bot.users.is_admin", return_value=False):
        assert is_onedrive_approver(555) is True


# ── get_user_role unchanged (regression) ──

def test_get_user_role_unchanged_admin(monkeypatch):
    """Adding chat to env list must NOT change get_user_role for admin."""
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "999")
    from bot.users import get_user_role, get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()
    with patch("bot.users.is_admin", return_value=True):
        assert get_user_role(999) == "admin"


def test_get_user_role_unchanged_unknown_in_env(monkeypatch):
    """Chat in env but no Redis record + not admin → still 'unknown'.
       Capability is orthogonal to role; gating happens at the OneDrive router."""
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "777")
    from bot.users import get_user_role, get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()
    with patch("bot.users.is_admin", return_value=False), \
         patch("bot.users.get_user", return_value=None):
        assert get_user_role(777) == "unknown"


# ── format_user_label ──

def test_format_user_label_with_username():
    from bot.users import format_user_label
    user = MagicMock()
    user.username = "joao"
    user.first_name = "João"
    user.id = 12345
    assert format_user_label(user) == "@joao"


def test_format_user_label_no_username():
    from bot.users import format_user_label
    user = MagicMock()
    user.username = None
    user.first_name = "Maria"
    user.id = 67890
    assert format_user_label(user) == "Maria"


def test_format_user_label_no_username_no_name():
    from bot.users import format_user_label
    user = MagicMock()
    user.username = None
    user.first_name = None
    user.id = 1234567890
    # Final fallback: last 4 digits of chat_id, prefixed with "Usuário"
    label = format_user_label(user)
    assert label.startswith("Usuário ")
    assert "7890" in label


def test_format_user_label_empty_string_username_falls_through():
    from bot.users import format_user_label
    user = MagicMock()
    user.username = ""
    user.first_name = "Carlos"
    user.id = 1
    assert format_user_label(user) == "Carlos"
```

- [ ] **Step 1.2: Run tests to verify they fail**

```bash
cd /Users/bigode/Dev/agentics_workflows
pytest tests/test_users_onedrive_approver.py -v
```

Expected: all FAIL with `ImportError: cannot import name 'get_onedrive_approver_ids' from 'bot.users'` (or similar).

- [ ] **Step 1.3: Add implementation to `webhook/bot/users.py`**

Append at the bottom of `webhook/bot/users.py` (after the existing `get_user_role` function, around line 185):

```python


# ── OneDrive approver capability (orthogonal to role enum) ──

import functools
import os


@functools.lru_cache(maxsize=1)
def get_onedrive_approver_ids() -> list[int]:
    """Parse ONEDRIVE_APPROVER_IDS env var → list of int chat_ids.

    - Empty / unset → [].
    - Whitespace tolerated.
    - Malformed items skipped with a single warning log.
    - Cached for process lifetime (env changes require redeploy).
    """
    raw = os.environ.get("ONEDRIVE_APPROVER_IDS", "").strip()
    if not raw:
        return []
    out: list[int] = []
    skipped: list[str] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            out.append(int(token))
        except ValueError:
            skipped.append(token)
    if skipped:
        logger.warning(
            "Skipped malformed ONEDRIVE_APPROVER_IDS entries: %s",
            ", ".join(skipped),
        )
    return out


def is_onedrive_approver(chat_id: int) -> bool:
    """True if chat_id is admin OR appears in ONEDRIVE_APPROVER_IDS env."""
    if is_admin(chat_id):
        return True
    return chat_id in get_onedrive_approver_ids()


def format_user_label(from_user) -> str:
    """Render a user's display label.

    Priority: @username → first_name → 'Usuário XXXX' (last 4 digits).
    Used in cascade card text — caller stores the result so it stays
    consistent across all edits even if the user later changes username.
    """
    username = getattr(from_user, "username", None) or ""
    first_name = getattr(from_user, "first_name", None) or ""
    if username:
        return f"@{username}"
    if first_name:
        return first_name
    chat_id = getattr(from_user, "id", 0) or 0
    return f"Usuário {str(chat_id)[-4:]}"
```

- [ ] **Step 1.4: Run tests to verify they pass**

```bash
cd /Users/bigode/Dev/agentics_workflows
pytest tests/test_users_onedrive_approver.py -v
```

Expected: all PASS.

- [ ] **Step 1.5: Commit**

```bash
git add webhook/bot/users.py tests/test_users_onedrive_approver.py
git commit -m "feat(bot): add onedrive approver capability helpers in users.py"
```

---

## Task 2: Document `ONEDRIVE_APPROVER_IDS` in `.env.example`

**Files:**
- Modify: `.env.example` (around the existing OneDrive section)

- [ ] **Step 2.1: Add the env var documentation**

Edit `.env.example` to add a new commented block right after the existing `GRAPH_FOLDER_PATH=` line. Use the Edit tool with this `old_string` (the line that already exists):

```
GRAPH_FOLDER_PATH=/SIGCM/4. Relatórios Mercado/Relatório Diário Minerals
```

Replace with:

```
GRAPH_FOLDER_PATH=/SIGCM/4. Relatórios Mercado/Relatório Diário Minerals

# Additional Telegram chat_ids that receive OneDrive approval cards
# (admin TELEGRAM_CHAT_ID is always implicitly included).
# Empty/unset → admin-only behavior (v1 OneDrive flow preserved).
# Up to 5 ids; any beyond that is supported but undocumented.
# Example: ONEDRIVE_APPROVER_IDS=456789012,234567890
ONEDRIVE_APPROVER_IDS=
```

- [ ] **Step 2.2: Verify the file**

```bash
grep -A 6 "ONEDRIVE_APPROVER_IDS" /Users/bigode/Dev/agentics_workflows/.env.example
```

Expected output: shows the documentation block + empty assignment.

- [ ] **Step 2.3: Commit**

```bash
git add .env.example
git commit -m "docs(env): document ONEDRIVE_APPROVER_IDS for multi-approver flow"
```

---

## Task 3: Fan-out + recipients persistence in `onedrive_pipeline.py`

**Files:**
- Modify: `webhook/onedrive_pipeline.py` (`process_notification` function only — lines 148–232)
- Modify: `tests/test_onedrive_pipeline.py` (add new tests)

- [ ] **Step 3.1: Write failing tests in `tests/test_onedrive_pipeline.py`**

Append to `tests/test_onedrive_pipeline.py`:

```python


# ── Multi-recipient fan-out tests (Task 3 of multi-approver plan) ──


@pytest.fixture
def fake_full_item():
    """Item shape after graph.get_item — has a downloadUrl."""
    return {
        "id": "item-multi-1",
        "name": "Multi_Test.pdf",
        "size": 9999,
        "file": {"mimeType": "application/pdf"},
        "@microsoft.graph.downloadUrl": "https://cdn.example.com/x?sig=multi",
    }


@pytest.mark.asyncio
async def test_send_approval_cards_admin_only_when_env_empty(
    monkeypatch, redis_client, fake_full_item, fake_contacts_repo
):
    """Empty ONEDRIVE_APPROVER_IDS → 1 send to admin, recipients=[admin]."""
    from onedrive_pipeline import _send_approval_cards
    monkeypatch.delenv("ONEDRIVE_APPROVER_IDS", raising=False)
    from bot.users import get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()

    bot = AsyncMock()
    bot.send_message = AsyncMock(side_effect=[
        MagicMock(message_id=1001),
    ])

    recipients = await _send_approval_cards(
        bot=bot,
        admin_chat_id=100,
        text="hello",
        keyboard=MagicMock(),
    )

    assert recipients == [{"chat_id": 100, "message_id": 1001}]
    assert bot.send_message.await_count == 1


@pytest.mark.asyncio
async def test_send_approval_cards_admin_plus_approvers(
    monkeypatch, fake_full_item
):
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "200,300")
    from onedrive_pipeline import _send_approval_cards
    from bot.users import get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()

    bot = AsyncMock()
    bot.send_message = AsyncMock(side_effect=[
        MagicMock(message_id=1001),
        MagicMock(message_id=2002),
        MagicMock(message_id=3003),
    ])

    recipients = await _send_approval_cards(
        bot=bot,
        admin_chat_id=100,
        text="hello",
        keyboard=MagicMock(),
    )

    chat_ids = sorted(r["chat_id"] for r in recipients)
    assert chat_ids == [100, 200, 300]
    assert bot.send_message.await_count == 3


@pytest.mark.asyncio
async def test_send_approval_cards_dedup_admin_in_env(monkeypatch):
    """Admin chat_id listed in env → still only one send to admin."""
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "100,200")
    from onedrive_pipeline import _send_approval_cards
    from bot.users import get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()

    bot = AsyncMock()
    bot.send_message = AsyncMock(side_effect=[
        MagicMock(message_id=1001),
        MagicMock(message_id=2002),
    ])

    recipients = await _send_approval_cards(
        bot=bot,
        admin_chat_id=100,
        text="hello",
        keyboard=MagicMock(),
    )

    chat_ids = sorted(r["chat_id"] for r in recipients)
    assert chat_ids == [100, 200]
    assert bot.send_message.await_count == 2


@pytest.mark.asyncio
async def test_send_approval_cards_partial_failure_continues(monkeypatch):
    """One approver send fails → others still proceed; recipients excludes the failure."""
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "200,300")
    from onedrive_pipeline import _send_approval_cards
    from bot.users import get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()

    class _Forbidden(Exception):
        pass

    bot = AsyncMock()
    bot.send_message = AsyncMock(side_effect=[
        MagicMock(message_id=1001),
        _Forbidden("blocked"),
        MagicMock(message_id=3003),
    ])

    recipients = await _send_approval_cards(
        bot=bot,
        admin_chat_id=100,
        text="hello",
        keyboard=MagicMock(),
    )

    chat_ids = sorted(r["chat_id"] for r in recipients)
    assert chat_ids == [100, 300]
    assert bot.send_message.await_count == 3


@pytest.mark.asyncio
async def test_send_approval_cards_admin_failure_returns_empty(monkeypatch):
    """If admin send fails too — return empty list, caller decides what to do."""
    monkeypatch.delenv("ONEDRIVE_APPROVER_IDS", raising=False)
    from onedrive_pipeline import _send_approval_cards
    from bot.users import get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()

    bot = AsyncMock()
    bot.send_message = AsyncMock(side_effect=Exception("network down"))

    recipients = await _send_approval_cards(
        bot=bot,
        admin_chat_id=100,
        text="hello",
        keyboard=MagicMock(),
    )

    assert recipients == []
    assert bot.send_message.await_count == 1


@pytest.mark.asyncio
async def test_persist_recipients_updates_approval_state(redis_client):
    """After _persist_recipients, approval state JSON has recipients array."""
    from onedrive_pipeline import _persist_recipients

    await redis_client.set(
        "approval:abc12",
        json.dumps({"status": "pending", "filename": "x.pdf"}),
        ex=48 * 3600,
    )

    await _persist_recipients(
        redis_client,
        approval_id="abc12",
        recipients=[
            {"chat_id": 100, "message_id": 1001},
            {"chat_id": 200, "message_id": 2002},
        ],
    )

    raw = await redis_client.get("approval:abc12")
    state = json.loads(raw)
    assert state["recipients"] == [
        {"chat_id": 100, "message_id": 1001},
        {"chat_id": 200, "message_id": 2002},
    ]
    assert state["filename"] == "x.pdf"  # other fields preserved
    ttl = await redis_client.ttl("approval:abc12")
    assert ttl > 0  # KEEPTTL preserved
```

- [ ] **Step 3.2: Run new tests to verify they fail**

```bash
cd /Users/bigode/Dev/agentics_workflows
pytest tests/test_onedrive_pipeline.py::test_send_approval_cards_admin_only_when_env_empty tests/test_onedrive_pipeline.py::test_persist_recipients_updates_approval_state -v
```

Expected: FAIL with `ImportError: cannot import name '_send_approval_cards' from 'onedrive_pipeline'`.

- [ ] **Step 3.3: Add the helpers + refactor `process_notification` in `webhook/onedrive_pipeline.py`**

First, add two new helper functions just above `process_notification` (around line 145, before the `# ── Main entrypoint ──` comment):

```python


async def _send_approval_cards(
    bot,
    admin_chat_id: int,
    text: str,
    keyboard,
) -> list[dict]:
    """Fan-out the approval card to admin + every approver in ONEDRIVE_APPROVER_IDS.

    Returns the list of `{chat_id, message_id}` for sends that succeeded.
    Failures are logged but never raise — partial fan-out is acceptable
    (other approvers + admin still receive the card).
    """
    import asyncio
    from bot.users import get_onedrive_approver_ids

    # Dedupe: admin always implicit, may also appear in the env list
    targets: list[int] = [admin_chat_id]
    for cid in get_onedrive_approver_ids():
        if cid not in targets:
            targets.append(cid)

    coros = [
        bot.send_message(
            chat_id=cid,
            text=text,
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
        for cid in targets
    ]
    results = await asyncio.gather(*coros, return_exceptions=True)

    recipients: list[dict] = []
    for cid, res in zip(targets, results):
        if isinstance(res, Exception):
            import logging
            logging.getLogger(__name__).warning(
                "OneDrive approval card send to %s failed: %s", cid, res
            )
            continue
        # res is the Message object returned by aiogram
        message_id = getattr(res, "message_id", None)
        if message_id is None:
            continue
        recipients.append({"chat_id": cid, "message_id": message_id})
    return recipients


async def _persist_recipients(
    redis_client, approval_id: str, recipients: list[dict]
) -> None:
    """Update approval:{uuid} JSON in place with recipients[]. Preserves TTL."""
    raw = await redis_client.get(f"approval:{approval_id}")
    if not raw:
        return
    state = json.loads(raw)
    state = {**state, "recipients": recipients}
    await redis_client.set(
        f"approval:{approval_id}",
        json.dumps(state),
        keepttl=True,
    )
```

Then modify the existing `process_notification` to use them. Find this block in `webhook/onedrive_pipeline.py` (currently around lines 198–227):

```python
        contacts_repo = ContactsRepo()
        bot = get_bot()
        admin_chat_id = int(os.environ["TELEGRAM_CHAT_ID"])

        for item in items:
            if not _is_pdf_file(item):
                continue
            if not await _is_new_item(redis_client, item["id"]):
                bus.emit("duplicate_webhook", detail={"item_id": item["id"]})
                continue
            await _mark_seen(redis_client, item["id"])

            # Delta responses omit @microsoft.graph.downloadUrl — we must
            # fetch the full driveItem to get a signed download URL.
            full_item = graph.get_item(drive_id, item["id"])

            approval_id = await create_approval_state(
                redis_client, full_item, drive_id=drive_id, trace_id=bus.trace_id
            )
            bus.emit("approval_created", detail={
                "approval_id": approval_id,
                "filename": full_item.get("name", item.get("name", "?")),
            })

            await bot.send_message(
                chat_id=admin_chat_id,
                text=build_approval_text(full_item),
                reply_markup=build_approval_keyboard(approval_id, contacts_repo),
                parse_mode="Markdown",
            )
```

Replace with:

```python
        contacts_repo = ContactsRepo()
        bot = get_bot()
        admin_chat_id = int(os.environ["TELEGRAM_CHAT_ID"])

        for item in items:
            if not _is_pdf_file(item):
                continue
            if not await _is_new_item(redis_client, item["id"]):
                bus.emit("duplicate_webhook", detail={"item_id": item["id"]})
                continue
            await _mark_seen(redis_client, item["id"])

            # Delta responses omit @microsoft.graph.downloadUrl — we must
            # fetch the full driveItem to get a signed download URL.
            full_item = graph.get_item(drive_id, item["id"])

            approval_id = await create_approval_state(
                redis_client, full_item, drive_id=drive_id, trace_id=bus.trace_id
            )
            bus.emit("approval_created", detail={
                "approval_id": approval_id,
                "filename": full_item.get("name", item.get("name", "?")),
            })

            recipients = await _send_approval_cards(
                bot=bot,
                admin_chat_id=admin_chat_id,
                text=build_approval_text(full_item),
                keyboard=build_approval_keyboard(approval_id, contacts_repo),
            )

            await _persist_recipients(redis_client, approval_id, recipients)

            if not recipients:
                bus.emit("approval_fanout_failed", level="error", detail={
                    "approval_id": approval_id,
                })
            elif len(recipients) < 1 + len(__import__("bot.users", fromlist=["get_onedrive_approver_ids"]).get_onedrive_approver_ids()):
                # Fewer recipients than expected (admin + approvers, deduplicated)
                bus.emit("approval_fanout_partial", level="warn", detail={
                    "approval_id": approval_id,
                    "delivered": len(recipients),
                    "recipients": recipients,
                })
            else:
                bus.emit("approval_fanout", detail={
                    "approval_id": approval_id,
                    "recipient_count": len(recipients),
                    "recipient_ids": [r["chat_id"] for r in recipients],
                })
```

> **Note on the import inside the elif:** the `__import__` form avoids a top-of-file import that could cause a circular import with `bot.users` (which itself imports from `bot.config`). If your linter flags this, an alternative is a top-level lazy import wrapped in a function — but `__import__` here is intentional and consistent with similar patterns elsewhere.

- [ ] **Step 3.4: Run all pipeline tests to verify they pass**

```bash
cd /Users/bigode/Dev/agentics_workflows
pytest tests/test_onedrive_pipeline.py -v
```

Expected: all PASS, including new tests + all previously existing tests.

- [ ] **Step 3.5: Commit**

```bash
git add webhook/onedrive_pipeline.py tests/test_onedrive_pipeline.py
git commit -m "feat(onedrive): fan-out approval card to admin + approvers, persist recipients"
```

---

## Task 4: Add `_claim` helper in `callbacks_onedrive.py`

**Files:**
- Modify: `webhook/bot/routers/callbacks_onedrive.py` (add helper near top, after `_save_state`)
- Modify: `tests/test_onedrive_callbacks.py` (add `_claim` tests)

- [ ] **Step 4.1: Write failing tests**

Append to `tests/test_onedrive_callbacks.py`:

```python


# ── Task 4: _claim helper tests ──


@pytest.mark.asyncio
async def test_claim_winner_path(redis_client):
    """First click on a fresh approval → returns ('won', claimer_dict)."""
    from bot.routers.callbacks_onedrive import _claim
    await redis_client.set(
        "approval:abc12",
        json.dumps({"status": "pending"}),
        ex=48 * 3600,
    )
    user = MagicMock()
    user.id = 100
    user.username = "joao"
    user.first_name = "João"

    status, claimer = await _claim(redis_client, "abc12", user)

    assert status == "won"
    assert claimer["chat_id"] == 100
    assert claimer["label"] == "@joao"
    # Persisted in Redis
    raw = await redis_client.get("approval:abc12:claimed_by")
    assert raw is not None
    persisted = json.loads(raw)
    assert persisted["chat_id"] == 100


@pytest.mark.asyncio
async def test_claim_loser_path(redis_client):
    """Second click by a different user → returns ('lost', original_claimer)."""
    from bot.routers.callbacks_onedrive import _claim
    await redis_client.set(
        "approval:abc12",
        json.dumps({"status": "pending"}),
        ex=48 * 3600,
    )
    # Pre-existing claim by user A
    await redis_client.set(
        "approval:abc12:claimed_by",
        json.dumps({"chat_id": 100, "label": "@joao", "claimed_at": "x"}),
        ex=48 * 3600,
    )

    user_b = MagicMock()
    user_b.id = 200
    user_b.username = "maria"
    user_b.first_name = "Maria"

    status, claimer = await _claim(redis_client, "abc12", user_b)

    assert status == "lost"
    assert claimer["chat_id"] == 100  # original claimer
    assert claimer["label"] == "@joao"


@pytest.mark.asyncio
async def test_claim_reentrant_path(redis_client):
    """Same user clicks twice → second call returns ('reentrant', self_claimer)."""
    from bot.routers.callbacks_onedrive import _claim
    await redis_client.set(
        "approval:abc12",
        json.dumps({"status": "pending"}),
        ex=48 * 3600,
    )
    user = MagicMock()
    user.id = 100
    user.username = "joao"
    user.first_name = "João"

    status1, _ = await _claim(redis_client, "abc12", user)
    status2, claimer2 = await _claim(redis_client, "abc12", user)

    assert status1 == "won"
    assert status2 == "reentrant"
    assert claimer2["chat_id"] == 100


@pytest.mark.asyncio
async def test_claim_inherits_approval_ttl(redis_client):
    """claimed_by key TTL ≈ approval key remaining TTL (within 5 s)."""
    from bot.routers.callbacks_onedrive import _claim
    await redis_client.set(
        "approval:abc12",
        json.dumps({"status": "pending"}),
        ex=48 * 3600,
    )
    user = MagicMock()
    user.id = 100
    user.username = "j"
    user.first_name = "J"

    await _claim(redis_client, "abc12", user)
    approval_ttl = await redis_client.ttl("approval:abc12")
    claim_ttl = await redis_client.ttl("approval:abc12:claimed_by")

    assert abs(approval_ttl - claim_ttl) <= 5
```

- [ ] **Step 4.2: Run tests to verify they fail**

```bash
cd /Users/bigode/Dev/agentics_workflows
pytest tests/test_onedrive_callbacks.py::test_claim_winner_path -v
```

Expected: FAIL with `ImportError: cannot import name '_claim'`.

- [ ] **Step 4.3: Add the `_claim` helper to `webhook/bot/routers/callbacks_onedrive.py`**

After the existing `_save_state` function (around line 45), add:

```python


async def _claim(redis_client, approval_id: str, from_user) -> tuple[str, dict]:
    """Atomic first-click lock.

    Returns one of:
      ("won", claimer_dict)        — this caller now owns the approval
      ("reentrant", claimer_dict)  — same user clicked again, still owns it
      ("lost", existing_claimer)   — another user already owns the approval

    The lock key is `approval:{uuid}:claimed_by`. TTL inherits the
    remaining TTL of `approval:{uuid}` so they expire together.
    """
    from bot.users import format_user_label
    from datetime import datetime, timezone

    approval_ttl = await redis_client.ttl(f"approval:{approval_id}")
    if approval_ttl <= 0:
        approval_ttl = 48 * 3600  # safety fallback if TTL info is missing

    payload = {
        "chat_id": from_user.id,
        "label": format_user_label(from_user),
        "claimed_at": datetime.now(timezone.utc).isoformat(),
    }
    ok = await redis_client.set(
        f"approval:{approval_id}:claimed_by",
        json.dumps(payload),
        nx=True,
        ex=approval_ttl,
    )
    if ok:
        return ("won", payload)

    raw = await redis_client.get(f"approval:{approval_id}:claimed_by")
    existing = json.loads(raw) if raw else {}
    if existing.get("chat_id") == from_user.id:
        return ("reentrant", existing)
    return ("lost", existing)
```

- [ ] **Step 4.4: Run tests to verify they pass**

```bash
cd /Users/bigode/Dev/agentics_workflows
pytest tests/test_onedrive_callbacks.py -v
```

Expected: all PASS, including new `_claim` tests + previously existing tests.

- [ ] **Step 4.5: Commit**

```bash
git add webhook/bot/routers/callbacks_onedrive.py tests/test_onedrive_callbacks.py
git commit -m "feat(onedrive): add atomic _claim helper for first-click race lock"
```

---

## Task 5: Add `_edit_others` cascade helper

**Files:**
- Modify: `webhook/bot/routers/callbacks_onedrive.py` (add helper near top)
- Modify: `tests/test_onedrive_callbacks.py` (add cascade tests)

- [ ] **Step 5.1: Write failing tests**

Append to `tests/test_onedrive_callbacks.py`:

```python


# ── Task 5: _edit_others cascade helper tests ──


@pytest.mark.asyncio
async def test_edit_others_skips_clicker(redis_client):
    from bot.routers.callbacks_onedrive import _edit_others
    state = {
        "filename": "x.pdf",
        "recipients": [
            {"chat_id": 100, "message_id": 1001},
            {"chat_id": 200, "message_id": 2002},
            {"chat_id": 300, "message_id": 3003},
        ],
    }
    await redis_client.set("approval:abc12", json.dumps(state), ex=48 * 3600)
    bot = AsyncMock()
    bus = MagicMock()

    await _edit_others(
        bot=bot,
        redis_client=redis_client,
        approval_id="abc12",
        new_text="hello",
        exclude_chat_id=200,
        bus=bus,
    )

    # Should edit chat_ids 100 and 300, skipping 200
    edited_chat_ids = sorted(
        c.kwargs["chat_id"] for c in bot.edit_message_text.await_args_list
    )
    assert edited_chat_ids == [100, 300]


@pytest.mark.asyncio
async def test_edit_others_no_recipients_is_noop(redis_client):
    from bot.routers.callbacks_onedrive import _edit_others
    state = {"filename": "x.pdf"}  # no recipients key
    await redis_client.set("approval:abc12", json.dumps(state), ex=48 * 3600)
    bot = AsyncMock()
    bus = MagicMock()

    await _edit_others(
        bot=bot, redis_client=redis_client, approval_id="abc12",
        new_text="hello", exclude_chat_id=999, bus=bus,
    )

    bot.edit_message_text.assert_not_called()


@pytest.mark.asyncio
async def test_edit_others_swallows_telegram_bad_request(redis_client):
    from bot.routers.callbacks_onedrive import _edit_others
    from aiogram.exceptions import TelegramBadRequest

    state = {
        "recipients": [
            {"chat_id": 200, "message_id": 2002},
            {"chat_id": 300, "message_id": 3003},
        ],
    }
    await redis_client.set("approval:abc12", json.dumps(state), ex=48 * 3600)
    bot = AsyncMock()
    bot.edit_message_text = AsyncMock(side_effect=[
        TelegramBadRequest(method=MagicMock(), message="message to edit not found"),
        None,
    ])
    bus = MagicMock()

    # Must not raise
    await _edit_others(
        bot=bot, redis_client=redis_client, approval_id="abc12",
        new_text="hello", exclude_chat_id=100, bus=bus,
    )

    # Bus emitted cascade_edit_skipped for the failed one
    skipped_calls = [
        c for c in bus.emit.call_args_list
        if c.args and c.args[0] == "cascade_edit_skipped"
    ]
    assert len(skipped_calls) == 1


@pytest.mark.asyncio
async def test_edit_others_emits_failed_for_unknown_exception(redis_client):
    from bot.routers.callbacks_onedrive import _edit_others

    state = {
        "recipients": [
            {"chat_id": 200, "message_id": 2002},
        ],
    }
    await redis_client.set("approval:abc12", json.dumps(state), ex=48 * 3600)
    bot = AsyncMock()
    bot.edit_message_text = AsyncMock(side_effect=RuntimeError("network"))
    bus = MagicMock()

    # Must not raise
    await _edit_others(
        bot=bot, redis_client=redis_client, approval_id="abc12",
        new_text="hello", exclude_chat_id=100, bus=bus,
    )

    failed_calls = [
        c for c in bus.emit.call_args_list
        if c.args and c.args[0] == "cascade_edit_failed"
    ]
    assert len(failed_calls) == 1
```

- [ ] **Step 5.2: Run tests to verify they fail**

```bash
cd /Users/bigode/Dev/agentics_workflows
pytest tests/test_onedrive_callbacks.py::test_edit_others_skips_clicker -v
```

Expected: FAIL with `ImportError: cannot import name '_edit_others'`.

- [ ] **Step 5.3: Add the `_edit_others` helper**

In `webhook/bot/routers/callbacks_onedrive.py`, just after the `_claim` helper added in Task 4, add:

```python


async def _edit_others(
    bot,
    redis_client,
    approval_id: str,
    new_text: str,
    exclude_chat_id: int,
    bus,
) -> None:
    """Cascade-edit every recipient card EXCEPT the clicker's.

    Reads recipients from `approval:{uuid}.recipients`. Edits in parallel
    via asyncio.gather. Swallows TelegramBadRequest (message gone, bot
    blocked, message not modified) and emits cascade_edit_skipped.
    Logs and emits cascade_edit_failed for unexpected errors. Never raises.

    Uses parse_mode=None deliberately — see spec, Markdown safety section.
    """
    import asyncio
    from aiogram.exceptions import TelegramBadRequest

    state = await _load_state(redis_client, approval_id)
    if not state:
        return
    recipients = state.get("recipients", []) or []
    targets = [r for r in recipients if r.get("chat_id") != exclude_chat_id]
    if not targets:
        return

    coros = [
        bot.edit_message_text(
            chat_id=r["chat_id"],
            message_id=r["message_id"],
            text=new_text,
            parse_mode=None,
            reply_markup=None,
        )
        for r in targets
    ]
    results = await asyncio.gather(*coros, return_exceptions=True)

    for r, exc in zip(targets, results):
        if isinstance(exc, TelegramBadRequest):
            bus.emit("cascade_edit_skipped", level="info", detail={
                "target_chat_id": r["chat_id"],
                "reason": str(exc)[:120],
            })
        elif isinstance(exc, Exception):
            bus.emit("cascade_edit_failed", level="warn", detail={
                "target_chat_id": r["chat_id"],
                "error": str(exc)[:200],
                "exc_type": type(exc).__name__,
            })
```

- [ ] **Step 5.4: Run tests to verify they pass**

```bash
cd /Users/bigode/Dev/agentics_workflows
pytest tests/test_onedrive_callbacks.py -v
```

Expected: all PASS.

- [ ] **Step 5.5: Commit**

```bash
git add webhook/bot/routers/callbacks_onedrive.py tests/test_onedrive_callbacks.py
git commit -m "feat(onedrive): add _edit_others cascade helper for non-clicker card edits"
```

---

## Task 6: Wire claim + cascade into `on_approve`

**Files:**
- Modify: `webhook/bot/routers/callbacks_onedrive.py` (`on_approve` function — current lines 56–102)
- Modify: `tests/test_onedrive_callbacks.py` (add tests for new behavior)

- [ ] **Step 6.1: Write failing tests**

Append to `tests/test_onedrive_callbacks.py`:

```python


# ── Task 6: on_approve race + cascade tests ──


@pytest.mark.asyncio
async def test_on_approve_winner_cascades_lock_message(
    mock_bot, mock_callback_query, redis_client
):
    """Winner clicks Lista X → other recipients get '🔒 Sendo decidido por @X'."""
    from bot.routers.callbacks_onedrive import on_approve
    from bot.callback_data import OneDriveApprove

    state = {
        "drive_item_id": "item1",
        "filename": "Test.pdf",
        "size": 1024,
        "downloadUrl": "https://x",
        "downloadUrl_fetched_at": "2026-04-22T00:00:00+00:00",
        "status": "pending",
        "created_at": "2026-04-22T00:00:00+00:00",
        "recipients": [
            {"chat_id": 100, "message_id": 1001},
            {"chat_id": 200, "message_id": 2002},
        ],
    }
    await redis_client.set("approval:abc12", json.dumps(state), ex=48 * 3600)

    cb_data = OneDriveApprove(approval_id="abc12", list_code="minerals_report")
    cb = mock_callback_query(user_id=100, chat_id=100, message_id=1001, data=cb_data.pack())
    cb.from_user.username = "admin"
    cb.from_user.first_name = "Admin"
    cb.bot = mock_bot

    mock_repo = MagicMock()
    mock_list = MagicMock(code="minerals_report", label="Minerals", member_count=3)
    mock_repo.list_lists.return_value = [mock_list]

    with patch("bot.routers.callbacks_onedrive._redis", return_value=redis_client), \
         patch("bot.routers.callbacks_onedrive.ContactsRepo", return_value=mock_repo):
        await on_approve(cb, cb_data)

    # Two edits: clicker's confirm screen + cascade lock to chat_id=200
    edited = mock_bot.edit_message_text.await_args_list
    edited_chats = sorted(c.kwargs["chat_id"] for c in edited)
    assert edited_chats == [100, 200]

    cascade_call = next(c for c in edited if c.kwargs["chat_id"] == 200)
    assert "Sendo decidido" in cascade_call.kwargs["text"]
    assert "@admin" in cascade_call.kwargs["text"]
    assert cascade_call.kwargs.get("parse_mode") is None


@pytest.mark.asyncio
async def test_on_approve_loser_only_toasts(
    mock_bot, mock_callback_query, redis_client
):
    """Loser (claim already held) → toast only, no edits."""
    from bot.routers.callbacks_onedrive import on_approve
    from bot.callback_data import OneDriveApprove

    state = {
        "drive_item_id": "item1", "filename": "Test.pdf", "size": 1,
        "downloadUrl": "x", "downloadUrl_fetched_at": "2026-04-22T00:00:00+00:00",
        "status": "pending", "created_at": "2026-04-22T00:00:00+00:00",
        "recipients": [
            {"chat_id": 100, "message_id": 1001},
            {"chat_id": 200, "message_id": 2002},
        ],
    }
    await redis_client.set("approval:abc12", json.dumps(state), ex=48 * 3600)
    # Pre-existing claim by user 100 (admin)
    await redis_client.set(
        "approval:abc12:claimed_by",
        json.dumps({"chat_id": 100, "label": "@admin", "claimed_at": "x"}),
        ex=48 * 3600,
    )

    cb_data = OneDriveApprove(approval_id="abc12", list_code="minerals_report")
    cb = mock_callback_query(user_id=200, chat_id=200, message_id=2002, data=cb_data.pack())
    cb.from_user.username = "colega"
    cb.from_user.first_name = "Colega"
    cb.bot = mock_bot

    with patch("bot.routers.callbacks_onedrive._redis", return_value=redis_client):
        await on_approve(cb, cb_data)

    # No edit — just answer with toast
    mock_bot.edit_message_text.assert_not_called()
    cb.answer.assert_called()
    toast = cb.answer.call_args.kwargs.get("text") or (
        cb.answer.call_args.args[0] if cb.answer.call_args.args else ""
    )
    assert "@admin" in toast
```

You also need to update one existing test that's now affected by the cascade. The existing test `test_on_approve_shows_confirm_screen` calls `mock_bot.edit_message_text.assert_called_once()` — but with the new fan-out it'll be called twice (clicker + cascade). Update it:

In `tests/test_onedrive_callbacks.py`, find the existing `test_on_approve_shows_confirm_screen` test and update its `seeded_pending_factory` to include recipients, OR update the assertion. Easiest: update the seeded fixture so `recipients` includes only the clicker (no cascade):

Replace the `seeded_pending_factory` fixture (currently at the top of the file) with:

```python
@pytest.fixture
def seeded_pending_factory(redis_client):
    """Returns an async setup func: await it inside each async test."""
    async def _setup(extra: dict | None = None):
        state = {
            "drive_id": "drive-test",
            "drive_item_id": "item-1",
            "filename": "Test.pdf",
            "size": 1024,
            "downloadUrl": "https://x",
            "downloadUrl_fetched_at": "2026-04-22T00:00:00+00:00",
            "status": "pending",
            "created_at": "2026-04-22T00:00:00+00:00",
            # Default: only the clicker is a recipient → cascade is a no-op
            "recipients": [{"chat_id": 12345, "message_id": 1}],
        }
        if extra:
            state.update(extra)
        await redis_client.set("approval:abc12", json.dumps(state), ex=48 * 3600)
        return "abc12"
    return _setup
```

The default `mock_callback_query` uses `user_id=12345, chat_id=12345, message_id=1` — so `recipients=[{"chat_id":12345,"message_id":1}]` makes the cascade a no-op for existing tests, preserving `assert_called_once`.

- [ ] **Step 6.2: Run tests to verify the new ones fail**

```bash
cd /Users/bigode/Dev/agentics_workflows
pytest tests/test_onedrive_callbacks.py::test_on_approve_winner_cascades_lock_message tests/test_onedrive_callbacks.py::test_on_approve_loser_only_toasts -v
```

Expected: FAIL — handler doesn't yet call `_claim` / `_edit_others`.

- [ ] **Step 6.3: Modify `on_approve` in `webhook/bot/routers/callbacks_onedrive.py`**

Replace the existing `on_approve` function body (currently lines 56–102) with:

```python
@callbacks_onedrive_router.callback_query(OneDriveApprove.filter())
async def on_approve(query: CallbackQuery, callback_data: OneDriveApprove):
    redis_client = _redis()
    state = await _load_state(redis_client, callback_data.approval_id)
    if not state:
        await query.answer(text="⚠️ Aprovação expirada", show_alert=True)
        return

    bus = EventBus(workflow="onedrive_webhook", trace_id=state.get("trace_id"))

    # Atomic first-click claim
    claim_status, claimer = await _claim(redis_client, callback_data.approval_id, query.from_user)

    if claim_status == "lost":
        bus.emit("approval_clashed", detail={
            "loser_chat_id": query.from_user.id,
            "winner_label": claimer.get("label"),
        })
        await query.answer(
            text=f"Já em decisão por {claimer.get('label', 'outro aprovador')}",
            show_alert=False,
        )
        return

    if claim_status == "won":
        bus.emit("approval_claimed", detail={
            "approval_id": callback_data.approval_id,
            "chat_id": claimer["chat_id"],
            "label": claimer["label"],
        })

    # Existing emission preserved
    bus.emit("approval_clicked", detail={
        "approval_id": callback_data.approval_id,
        "list_code": callback_data.list_code,
    })

    contacts_repo = ContactsRepo()
    label, count = _list_label(callback_data.list_code, contacts_repo)

    state = {**state, "status": "awaiting_confirm"}
    await _save_state(redis_client, callback_data.approval_id, state)

    text = (
        f"⚠️ *Confirmar envio?*\n\n"
        f"`{state['filename']}`\n"
        f"→ {label} ({count} contatos)"
    )
    kb = InlineKeyboardBuilder()
    kb.button(
        text="✅ Enviar",
        callback_data=OneDriveConfirm(
            approval_id=callback_data.approval_id,
            list_code=callback_data.list_code,
        ).pack(),
    )
    kb.button(
        text="◀ Voltar",
        callback_data=OneDriveDiscard(approval_id=callback_data.approval_id).pack(),
    )
    kb.adjust(2)

    await query.bot.edit_message_text(
        text=text,
        chat_id=query.message.chat.id,
        message_id=query.message.message_id,
        reply_markup=kb.as_markup(),
        parse_mode="Markdown",
    )

    # Cascade lock to other recipients (only if this was a fresh claim;
    # reentrant means cards are already locked from a prior click).
    if claim_status == "won":
        from datetime import datetime, timezone
        hhmm = datetime.now(timezone.utc).strftime("%H:%M")
        cascade_text = f"🔒 Sendo decidido por {claimer['label']} às {hhmm}"
        await _edit_others(
            bot=query.bot,
            redis_client=redis_client,
            approval_id=callback_data.approval_id,
            new_text=cascade_text,
            exclude_chat_id=query.from_user.id,
            bus=bus,
        )

    await query.answer()
```

- [ ] **Step 6.4: Run all callback tests**

```bash
cd /Users/bigode/Dev/agentics_workflows
pytest tests/test_onedrive_callbacks.py -v
```

Expected: all PASS, including the 2 new tests + existing `test_on_approve_*` + `test_expired_approval_shows_warning`.

- [ ] **Step 6.5: Commit**

```bash
git add webhook/bot/routers/callbacks_onedrive.py tests/test_onedrive_callbacks.py
git commit -m "feat(onedrive): on_approve now claims lock and cascades 🔒 to other recipients"
```

---

## Task 7: Wire claim + cascade into `on_confirm`

**Files:**
- Modify: `webhook/bot/routers/callbacks_onedrive.py` (`on_confirm` function — current lines 105–188)
- Modify: `tests/test_onedrive_callbacks.py` (add tests)

- [ ] **Step 7.1: Write failing tests**

Append to `tests/test_onedrive_callbacks.py`:

```python


# ── Task 7: on_confirm cascade tests ──


@pytest.mark.asyncio
async def test_on_confirm_cascades_final_result_to_others(
    mock_bot, mock_callback_query, redis_client
):
    """After successful dispatch, non-clicker recipients see '✏️ Decidido por … ✅ N/M'."""
    from bot.routers.callbacks_onedrive import on_confirm
    from bot.callback_data import OneDriveConfirm

    state = {
        "drive_item_id": "item1", "filename": "Test.pdf", "size": 1,
        "downloadUrl": "x", "downloadUrl_fetched_at": "2026-04-22T00:00:00+00:00",
        "status": "awaiting_confirm", "created_at": "2026-04-22T00:00:00+00:00",
        "recipients": [
            {"chat_id": 100, "message_id": 1001},
            {"chat_id": 200, "message_id": 2002},
        ],
    }
    await redis_client.set("approval:abc12", json.dumps(state), ex=48 * 3600)
    # Pre-claimed by clicker 100
    await redis_client.set(
        "approval:abc12:claimed_by",
        json.dumps({"chat_id": 100, "label": "@admin", "claimed_at": "x"}),
        ex=48 * 3600,
    )

    cb_data = OneDriveConfirm(approval_id="abc12", list_code="minerals_report")
    cb = mock_callback_query(user_id=100, chat_id=100, message_id=1001, data=cb_data.pack())
    cb.from_user.username = "admin"
    cb.from_user.first_name = "Admin"
    cb.bot = mock_bot

    mock_dispatch = AsyncMock(return_value={"sent": 3, "failed": 0, "skipped": 0})
    mock_repo = MagicMock()
    mock_list = MagicMock(code="minerals_report", label="Minerals", member_count=3)
    mock_repo.list_lists.return_value = [mock_list]

    with patch("bot.routers.callbacks_onedrive._redis", return_value=redis_client), \
         patch("bot.routers.callbacks_onedrive.ContactsRepo", return_value=mock_repo), \
         patch("bot.routers.callbacks_onedrive.dispatch_document", mock_dispatch):
        await on_confirm(cb, cb_data)

    edits = mock_bot.edit_message_text.await_args_list
    edited_chats = sorted(c.kwargs["chat_id"] for c in edits)
    # Clicker (100) gets at least one edit ("Enviando..." then "Enviado")
    # Other recipient (200) gets exactly one cascade edit at the end
    assert 100 in edited_chats
    assert 200 in edited_chats

    cascade = next(c for c in edits if c.kwargs["chat_id"] == 200)
    assert "Decidido por" in cascade.kwargs["text"]
    assert "@admin" in cascade.kwargs["text"]
    assert "3/3" in cascade.kwargs["text"] or "3 / 3" in cascade.kwargs["text"]
    assert cascade.kwargs.get("parse_mode") is None


@pytest.mark.asyncio
async def test_on_confirm_cascades_failure_to_others(
    mock_bot, mock_callback_query, redis_client
):
    """When dispatch fails entirely, non-clickers see failure cascade."""
    from bot.routers.callbacks_onedrive import on_confirm
    from bot.callback_data import OneDriveConfirm

    state = {
        "drive_item_id": "item1", "filename": "Test.pdf", "size": 1,
        "downloadUrl": "x", "downloadUrl_fetched_at": "2026-04-22T00:00:00+00:00",
        "status": "awaiting_confirm", "created_at": "2026-04-22T00:00:00+00:00",
        "recipients": [
            {"chat_id": 100, "message_id": 1001},
            {"chat_id": 200, "message_id": 2002},
        ],
    }
    await redis_client.set("approval:abc12", json.dumps(state), ex=48 * 3600)
    await redis_client.set(
        "approval:abc12:claimed_by",
        json.dumps({"chat_id": 100, "label": "@admin", "claimed_at": "x"}),
        ex=48 * 3600,
    )

    cb_data = OneDriveConfirm(approval_id="abc12", list_code="minerals_report")
    cb = mock_callback_query(user_id=100, chat_id=100, message_id=1001, data=cb_data.pack())
    cb.from_user.username = "admin"
    cb.from_user.first_name = "Admin"
    cb.bot = mock_bot

    # Dispatch raises → handler catches and renders failure card
    mock_dispatch = AsyncMock(side_effect=RuntimeError("PDF download failed"))
    mock_repo = MagicMock()
    mock_repo.list_lists.return_value = [
        MagicMock(code="minerals_report", label="Minerals", member_count=3)
    ]

    with patch("bot.routers.callbacks_onedrive._redis", return_value=redis_client), \
         patch("bot.routers.callbacks_onedrive.ContactsRepo", return_value=mock_repo), \
         patch("bot.routers.callbacks_onedrive.dispatch_document", mock_dispatch):
        await on_confirm(cb, cb_data)

    edits = mock_bot.edit_message_text.await_args_list
    cascade = next((c for c in edits if c.kwargs["chat_id"] == 200), None)
    assert cascade is not None
    assert "Falha" in cascade.kwargs["text"] or "❌" in cascade.kwargs["text"]
    assert "@admin" in cascade.kwargs["text"]


@pytest.mark.asyncio
async def test_on_confirm_deletes_both_redis_keys_after_success(
    mock_bot, mock_callback_query, redis_client
):
    from bot.routers.callbacks_onedrive import on_confirm
    from bot.callback_data import OneDriveConfirm

    state = {
        "drive_item_id": "item1", "filename": "Test.pdf", "size": 1,
        "downloadUrl": "x", "downloadUrl_fetched_at": "2026-04-22T00:00:00+00:00",
        "status": "awaiting_confirm", "created_at": "2026-04-22T00:00:00+00:00",
        "recipients": [{"chat_id": 100, "message_id": 1001}],
    }
    await redis_client.set("approval:abc12", json.dumps(state), ex=48 * 3600)
    await redis_client.set(
        "approval:abc12:claimed_by",
        json.dumps({"chat_id": 100, "label": "@admin", "claimed_at": "x"}),
        ex=48 * 3600,
    )

    cb_data = OneDriveConfirm(approval_id="abc12", list_code="minerals_report")
    cb = mock_callback_query(user_id=100, chat_id=100, message_id=1001, data=cb_data.pack())
    cb.from_user.username = "admin"
    cb.bot = mock_bot

    mock_dispatch = AsyncMock(return_value={"sent": 1, "failed": 0, "skipped": 0})
    mock_repo = MagicMock()
    mock_repo.list_lists.return_value = [
        MagicMock(code="minerals_report", label="Minerals", member_count=1)
    ]

    with patch("bot.routers.callbacks_onedrive._redis", return_value=redis_client), \
         patch("bot.routers.callbacks_onedrive.ContactsRepo", return_value=mock_repo), \
         patch("bot.routers.callbacks_onedrive.dispatch_document", mock_dispatch):
        await on_confirm(cb, cb_data)

    assert (await redis_client.get("approval:abc12")) is None
    assert (await redis_client.get("approval:abc12:claimed_by")) is None
```

- [ ] **Step 7.2: Run tests to verify they fail**

```bash
cd /Users/bigode/Dev/agentics_workflows
pytest tests/test_onedrive_callbacks.py::test_on_confirm_cascades_final_result_to_others tests/test_onedrive_callbacks.py::test_on_confirm_cascades_failure_to_others tests/test_onedrive_callbacks.py::test_on_confirm_deletes_both_redis_keys_after_success -v
```

Expected: at least the cascade tests FAIL (handler doesn't cascade yet).

- [ ] **Step 7.3: Modify `on_confirm` in `webhook/bot/routers/callbacks_onedrive.py`**

Replace the existing `on_confirm` function body (currently lines 105–188) with:

```python
@callbacks_onedrive_router.callback_query(OneDriveConfirm.filter())
async def on_confirm(query: CallbackQuery, callback_data: OneDriveConfirm):
    redis_client = _redis()
    state = await _load_state(redis_client, callback_data.approval_id)
    if not state:
        await query.answer(text="⚠️ Aprovação expirada", show_alert=True)
        return
    if state.get("status") == "dispatching":
        await query.answer(text="Já em andamento…", show_alert=True)
        return

    # Reentrant claim — same user already owns this approval from on_approve.
    # If a different user somehow reaches on_confirm (shouldn't happen via UI
    # because cascade locked their card), reject defensively.
    bus = EventBus(workflow="onedrive_webhook", trace_id=state.get("trace_id"))
    claim_status, claimer = await _claim(redis_client, callback_data.approval_id, query.from_user)
    if claim_status == "lost":
        bus.emit("approval_clashed", detail={
            "loser_chat_id": query.from_user.id,
            "winner_label": claimer.get("label"),
        })
        await query.answer(
            text=f"Já em decisão por {claimer.get('label', 'outro aprovador')}",
            show_alert=False,
        )
        return

    bus.emit("approval_approved", detail={
        "approval_id": callback_data.approval_id,
        "list_code": callback_data.list_code,
    })

    state = {**state, "status": "dispatching"}
    await _save_state(redis_client, callback_data.approval_id, state)

    contacts_repo = ContactsRepo()
    label, _ = _list_label(callback_data.list_code, contacts_repo)

    await query.bot.edit_message_text(
        text=f"📤 Enviando *{state['filename']}* → {label}…",
        chat_id=query.message.chat.id,
        message_id=query.message.message_id,
        parse_mode="Markdown",
    )
    await query.answer()

    try:
        result = await dispatch_document(
            approval_id=callback_data.approval_id,
            list_code=callback_data.list_code,
        )
    except Exception as exc:
        logger.exception("dispatch_document failed")
        bus.emit("dispatch_failed", level="error", detail={
            "approval_id": callback_data.approval_id,
            "error": str(exc)[:200],
        })
        await query.bot.edit_message_text(
            text=f"❌ Falha no envio: {type(exc).__name__}: {str(exc)[:200]}",
            chat_id=query.message.chat.id,
            message_id=query.message.message_id,
            parse_mode=None,
        )
        # Cascade failure to other recipients
        cascade_text = (
            f"❌ Decidido por {claimer.get('label', '?')} → {label}\n"
            f"Falha no envio"
        )
        await _edit_others(
            bot=query.bot, redis_client=redis_client,
            approval_id=callback_data.approval_id, new_text=cascade_text,
            exclude_chat_id=query.from_user.id, bus=bus,
        )
        await redis_client.delete(f"approval:{callback_data.approval_id}")
        await redis_client.delete(f"approval:{callback_data.approval_id}:claimed_by")
        return

    total = result["sent"] + result["failed"] + result["skipped"]
    if result["failed"] and not result["sent"]:
        icon = "❌"
        header = "Falhou"
    elif result["failed"]:
        icon = "⚠️"
        header = "Parcial"
    else:
        icon = "✅"
        header = "Enviado"
    summary = (
        f"{icon} *{header}* — {state['filename']}\n"
        f"Lista: {label}\n"
        f"{result['sent']}/{total} sucesso"
    )
    if result["failed"]:
        summary += f" · {result['failed']} falhas"
    if result["skipped"]:
        summary += f" · {result['skipped']} já enviados antes"

    if result["failed"] and not result["sent"] and result.get("errors"):
        first = result["errors"][0]
        summary += f"\n\n⚠️ Erro: `{first.get('error','')[:200]}`"

    await query.bot.edit_message_text(
        text=summary,
        chat_id=query.message.chat.id,
        message_id=query.message.message_id,
        parse_mode="Markdown",
    )

    # Cascade final result to other recipients (parse_mode=None — no Markdown)
    cascade_text = (
        f"✏️ Decidido por {claimer.get('label', '?')} → {label}\n"
        f"{icon} {result['sent']}/{total}"
    )
    if result["failed"]:
        cascade_text += f" · {result['failed']} falhas"
    await _edit_others(
        bot=query.bot, redis_client=redis_client,
        approval_id=callback_data.approval_id, new_text=cascade_text,
        exclude_chat_id=query.from_user.id, bus=bus,
    )

    await redis_client.delete(f"approval:{callback_data.approval_id}")
    await redis_client.delete(f"approval:{callback_data.approval_id}:claimed_by")
```

- [ ] **Step 7.4: Run all callback tests**

```bash
cd /Users/bigode/Dev/agentics_workflows
pytest tests/test_onedrive_callbacks.py -v
```

Expected: all PASS, including all new + existing tests.

> **Note:** the existing `test_on_confirm_triggers_dispatch` should still pass because the default `seeded_pending_factory` now includes `recipients=[{"chat_id":12345,"message_id":1}]` (only the clicker), so cascade is a no-op. The test's `mock_dispatch.assert_awaited_once()` is preserved.

- [ ] **Step 7.5: Commit**

```bash
git add webhook/bot/routers/callbacks_onedrive.py tests/test_onedrive_callbacks.py
git commit -m "feat(onedrive): on_confirm cascades final result (success or failure) to non-clicker recipients"
```

---

## Task 8: Wire claim + cascade into `on_discard`

**Files:**
- Modify: `webhook/bot/routers/callbacks_onedrive.py` (`on_discard` — current lines 191–209)
- Modify: `tests/test_onedrive_callbacks.py`

- [ ] **Step 8.1: Write failing tests**

Append to `tests/test_onedrive_callbacks.py`:

```python


# ── Task 8: on_discard cascade tests ──


@pytest.mark.asyncio
async def test_on_discard_cascades_to_others_skipping_lock_state(
    mock_bot, mock_callback_query, redis_client
):
    """Discard goes directly to '❌ Descartado por @X' on others (no intermediate 🔒)."""
    from bot.routers.callbacks_onedrive import on_discard
    from bot.callback_data import OneDriveDiscard

    state = {
        "drive_item_id": "item1", "filename": "Test.pdf", "size": 1,
        "downloadUrl": "x", "downloadUrl_fetched_at": "2026-04-22T00:00:00+00:00",
        "status": "pending", "created_at": "2026-04-22T00:00:00+00:00",
        "recipients": [
            {"chat_id": 100, "message_id": 1001},
            {"chat_id": 200, "message_id": 2002},
        ],
    }
    await redis_client.set("approval:abc12", json.dumps(state), ex=48 * 3600)

    cb_data = OneDriveDiscard(approval_id="abc12")
    cb = mock_callback_query(user_id=100, chat_id=100, message_id=1001, data=cb_data.pack())
    cb.from_user.username = "admin"
    cb.from_user.first_name = "Admin"
    cb.bot = mock_bot

    with patch("bot.routers.callbacks_onedrive._redis", return_value=redis_client):
        await on_discard(cb, cb_data)

    edits = mock_bot.edit_message_text.await_args_list
    cascade = next((c for c in edits if c.kwargs["chat_id"] == 200), None)
    assert cascade is not None
    assert "Descartado por" in cascade.kwargs["text"]
    assert "@admin" in cascade.kwargs["text"]
    assert "Test.pdf" in cascade.kwargs["text"]
    assert cascade.kwargs.get("parse_mode") is None


@pytest.mark.asyncio
async def test_on_discard_deletes_both_keys(
    mock_bot, mock_callback_query, redis_client
):
    from bot.routers.callbacks_onedrive import on_discard
    from bot.callback_data import OneDriveDiscard

    state = {
        "drive_item_id": "item1", "filename": "Test.pdf", "size": 1,
        "downloadUrl": "x", "downloadUrl_fetched_at": "2026-04-22T00:00:00+00:00",
        "status": "pending", "created_at": "2026-04-22T00:00:00+00:00",
        "recipients": [{"chat_id": 100, "message_id": 1001}],
    }
    await redis_client.set("approval:abc12", json.dumps(state), ex=48 * 3600)
    await redis_client.set(
        "approval:abc12:claimed_by",
        json.dumps({"chat_id": 100, "label": "@admin", "claimed_at": "x"}),
        ex=48 * 3600,
    )

    cb_data = OneDriveDiscard(approval_id="abc12")
    cb = mock_callback_query(user_id=100, data=cb_data.pack())
    cb.from_user.username = "admin"
    cb.bot = mock_bot

    with patch("bot.routers.callbacks_onedrive._redis", return_value=redis_client):
        await on_discard(cb, cb_data)

    assert (await redis_client.get("approval:abc12")) is None
    assert (await redis_client.get("approval:abc12:claimed_by")) is None


@pytest.mark.asyncio
async def test_on_discard_loser_path(
    mock_bot, mock_callback_query, redis_client
):
    """Discard click when claim already held by someone else → toast only."""
    from bot.routers.callbacks_onedrive import on_discard
    from bot.callback_data import OneDriveDiscard

    state = {
        "filename": "Test.pdf",
        "recipients": [
            {"chat_id": 100, "message_id": 1001},
            {"chat_id": 200, "message_id": 2002},
        ],
    }
    await redis_client.set("approval:abc12", json.dumps(state), ex=48 * 3600)
    await redis_client.set(
        "approval:abc12:claimed_by",
        json.dumps({"chat_id": 100, "label": "@admin", "claimed_at": "x"}),
        ex=48 * 3600,
    )

    cb_data = OneDriveDiscard(approval_id="abc12")
    cb = mock_callback_query(user_id=200, data=cb_data.pack())
    cb.from_user.username = "colega"
    cb.from_user.first_name = "Colega"
    cb.bot = mock_bot

    with patch("bot.routers.callbacks_onedrive._redis", return_value=redis_client):
        await on_discard(cb, cb_data)

    mock_bot.edit_message_text.assert_not_called()
    cb.answer.assert_called()
    toast = cb.answer.call_args.kwargs.get("text") or (
        cb.answer.call_args.args[0] if cb.answer.call_args.args else ""
    )
    assert "@admin" in toast
    # Approval state must NOT be deleted by a losing click
    assert (await redis_client.get("approval:abc12")) is not None
```

- [ ] **Step 8.2: Run tests to verify they fail**

```bash
cd /Users/bigode/Dev/agentics_workflows
pytest tests/test_onedrive_callbacks.py::test_on_discard_cascades_to_others_skipping_lock_state tests/test_onedrive_callbacks.py::test_on_discard_loser_path -v
```

Expected: FAIL — handler doesn't yet claim or cascade.

> **Note:** `test_on_discard_deletes_both_keys` may already pass — current handler deletes `approval:{uuid}` but not `:claimed_by`. The new test will catch the missing `claimed_by` deletion.

- [ ] **Step 8.3: Modify `on_discard` in `webhook/bot/routers/callbacks_onedrive.py`**

Replace the existing `on_discard` function body (currently lines 191–209) with:

```python
@callbacks_onedrive_router.callback_query(OneDriveDiscard.filter())
async def on_discard(query: CallbackQuery, callback_data: OneDriveDiscard):
    redis_client = _redis()
    state = await _load_state(redis_client, callback_data.approval_id)
    filename = state.get("filename", "(expirado)") if state else "(expirado)"

    bus = EventBus(workflow="onedrive_webhook", trace_id=(state or {}).get("trace_id"))

    if state:
        # Race-safe claim — discard is also a "decision" that locks the approval
        claim_status, claimer = await _claim(redis_client, callback_data.approval_id, query.from_user)
        if claim_status == "lost":
            bus.emit("approval_clashed", detail={
                "loser_chat_id": query.from_user.id,
                "winner_label": claimer.get("label"),
            })
            await query.answer(
                text=f"Já em decisão por {claimer.get('label', 'outro aprovador')}",
                show_alert=False,
            )
            return

        bus.emit("approval_discarded", detail={"approval_id": callback_data.approval_id})

        # Cascade discard message to other recipients (skip 🔒 — terminal in one step)
        from datetime import datetime, timezone
        hhmm = datetime.now(timezone.utc).strftime("%H:%M")
        cascade_text = f"❌ Descartado por {claimer['label']} às {hhmm}\n{filename}"
        await _edit_others(
            bot=query.bot, redis_client=redis_client,
            approval_id=callback_data.approval_id, new_text=cascade_text,
            exclude_chat_id=query.from_user.id, bus=bus,
        )

    await redis_client.delete(f"approval:{callback_data.approval_id}")
    await redis_client.delete(f"approval:{callback_data.approval_id}:claimed_by")

    from datetime import datetime
    await query.bot.edit_message_text(
        text=f"❌ Descartado às {datetime.now().strftime('%H:%M')}\n`{filename}`",
        chat_id=query.message.chat.id,
        message_id=query.message.message_id,
        parse_mode="Markdown",
    )
    await query.answer()
```

- [ ] **Step 8.4: Run all callback tests**

```bash
cd /Users/bigode/Dev/agentics_workflows
pytest tests/test_onedrive_callbacks.py -v
```

Expected: all PASS.

- [ ] **Step 8.5: Commit**

```bash
git add webhook/bot/routers/callbacks_onedrive.py tests/test_onedrive_callbacks.py
git commit -m "feat(onedrive): on_discard claims lock and cascades discard to other recipients"
```

---

## Task 9: Replace router middleware with capability filter

**Files:**
- Modify: `webhook/bot/routers/callbacks_onedrive.py` (router setup at top — current lines 23–26)
- Modify: `tests/test_onedrive_callbacks.py`

The current setup gates the entire router by `RoleMiddleware({"admin"})`. We need to replace this with per-handler aiogram filter `F.func(...)` that calls `is_onedrive_approver`. This way, an approver added to env who is also a `subscriber` can still trigger handlers — the role enum doesn't matter.

- [ ] **Step 9.1: Write failing test**

Append to `tests/test_onedrive_callbacks.py`:

```python


# ── Task 9: capability filter integration test ──


def test_router_uses_capability_filter_not_role_middleware():
    """Sanity check: router has no admin-only middleware; gating is per-handler."""
    from bot.routers import callbacks_onedrive

    # No callback_query middleware should be attached at the router level
    # for role-based gating (we use per-handler filters instead).
    middlewares = list(callbacks_onedrive.callbacks_onedrive_router.callback_query.middleware._middlewares)
    role_mws = [m for m in middlewares if type(m).__name__ == "RoleMiddleware"]
    assert role_mws == [], (
        "callbacks_onedrive_router must not gate by RoleMiddleware "
        "— gating is per-handler via is_onedrive_approver capability filter"
    )
```

- [ ] **Step 9.2: Run test to verify it fails**

```bash
cd /Users/bigode/Dev/agentics_workflows
pytest tests/test_onedrive_callbacks.py::test_router_uses_capability_filter_not_role_middleware -v
```

Expected: FAIL because the router currently has `callbacks_onedrive_router.callback_query.middleware(RoleMiddleware(...))` attached.

- [ ] **Step 9.3: Replace middleware with per-handler filters in `webhook/bot/routers/callbacks_onedrive.py`**

Find the router setup at the top of the file (around lines 23–26):

```python
callbacks_onedrive_router = Router(name="callbacks_onedrive")
callbacks_onedrive_router.callback_query.middleware(
    RoleMiddleware(allowed_roles={"admin"})
)
```

Replace with:

```python
callbacks_onedrive_router = Router(name="callbacks_onedrive")
# Authorization is per-handler via is_onedrive_approver capability filter
# (added to each callback_query decorator below). RoleMiddleware is intentionally
# NOT attached here — the capability is orthogonal to the existing role enum.
```

You can also remove the now-unused `from bot.middlewares.auth import RoleMiddleware` import at the top.

Now add the `F.func` filter to each of the three handler decorators. Find:

```python
@callbacks_onedrive_router.callback_query(OneDriveApprove.filter())
async def on_approve(query: CallbackQuery, callback_data: OneDriveApprove):
```

Replace with:

```python
@callbacks_onedrive_router.callback_query(
    OneDriveApprove.filter(),
    F.func(lambda q: is_onedrive_approver(q.from_user.id)),
)
async def on_approve(query: CallbackQuery, callback_data: OneDriveApprove):
```

Do the same for `on_confirm`:

```python
@callbacks_onedrive_router.callback_query(
    OneDriveConfirm.filter(),
    F.func(lambda q: is_onedrive_approver(q.from_user.id)),
)
async def on_confirm(query: CallbackQuery, callback_data: OneDriveConfirm):
```

And `on_discard`:

```python
@callbacks_onedrive_router.callback_query(
    OneDriveDiscard.filter(),
    F.func(lambda q: is_onedrive_approver(q.from_user.id)),
)
async def on_discard(query: CallbackQuery, callback_data: OneDriveDiscard):
```

Add the necessary imports near the top of the file (alongside the existing aiogram imports):

```python
from aiogram import F, Router

from bot.users import is_onedrive_approver
```

(The `Router` import already exists; add `F` to it. `is_onedrive_approver` is the new import.)

- [ ] **Step 9.4: Run all callback tests + middleware test**

```bash
cd /Users/bigode/Dev/agentics_workflows
pytest tests/test_onedrive_callbacks.py -v
```

Expected: all PASS, including the new middleware-absence test + all existing tests.

- [ ] **Step 9.5: Commit**

```bash
git add webhook/bot/routers/callbacks_onedrive.py tests/test_onedrive_callbacks.py
git commit -m "refactor(onedrive): replace RoleMiddleware with is_onedrive_approver capability filter"
```

---

## Task 10: `/start` early return for approver-only users

**Files:**
- Modify: `webhook/bot/routers/onboarding.py` (`cmd_start` function — current lines 32–83)
- Create: `tests/test_onboarding_approver.py`

- [ ] **Step 10.1: Write failing test**

Create `tests/test_onboarding_approver.py`:

```python
"""Tests for /start early return when user is an OneDrive approver only."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest


@pytest.mark.asyncio
async def test_start_approver_only_does_not_create_pending(monkeypatch, mock_message):
    """User in ONEDRIVE_APPROVER_IDS who has no Redis record and isn't admin
    sees a fixed welcome and is NOT registered as pending."""
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "555")
    from bot.users import get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()

    from bot.routers.onboarding import cmd_start

    msg = mock_message(chat_id=555, user_id=555)
    msg.from_user.full_name = "Colega"
    msg.from_user.username = "colega"

    create_pending_mock = MagicMock()

    with patch("bot.routers.onboarding.is_admin", return_value=False), \
         patch("bot.routers.onboarding.get_user_role", return_value="unknown"), \
         patch("bot.routers.onboarding.get_user", return_value=None), \
         patch("bot.routers.onboarding.create_pending_user", create_pending_mock), \
         patch("bot.routers.onboarding.get_bot", return_value=AsyncMock()):
        await cmd_start(msg)

    msg.answer.assert_called_once()
    welcome = msg.answer.call_args.args[0] if msg.answer.call_args.args else ""
    assert "aprovador" in welcome.lower() or "onedrive" in welcome.lower() or "sharepoint" in welcome.lower()
    create_pending_mock.assert_not_called()


@pytest.mark.asyncio
async def test_start_approver_who_is_admin_uses_admin_path(monkeypatch, mock_message):
    """Admin who is also in env list still gets the admin welcome (admin role wins)."""
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "999")
    from bot.users import get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()

    from bot.routers.onboarding import cmd_start

    msg = mock_message(chat_id=999, user_id=999)
    msg.from_user.full_name = "Admin"
    msg.from_user.username = "admin"

    with patch("bot.routers.onboarding.get_user_role", return_value="admin"), \
         patch("bot.routers.onboarding.is_admin", return_value=True), \
         patch("bot.routers.onboarding.build_reply_keyboard", return_value=MagicMock()):
        await cmd_start(msg)

    msg.answer.assert_called_once()
    welcome = msg.answer.call_args.args[0] if msg.answer.call_args.args else ""
    assert "admin" in welcome.lower()


@pytest.mark.asyncio
async def test_start_subscriber_in_env_uses_subscriber_path(monkeypatch, mock_message):
    """Existing subscriber who is also in env still uses subscriber welcome."""
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "888")
    from bot.users import get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()

    from bot.routers.onboarding import cmd_start

    msg = mock_message(chat_id=888, user_id=888)
    msg.from_user.full_name = "Sub"

    with patch("bot.routers.onboarding.get_user_role", return_value="subscriber"), \
         patch("bot.routers.onboarding.is_admin", return_value=False), \
         patch("bot.routers.onboarding.build_reply_keyboard", return_value=MagicMock()):
        await cmd_start(msg)

    welcome = msg.answer.call_args.args[0] if msg.answer.call_args.args else ""
    assert "volta" in welcome.lower() or "bem vindo" in welcome.lower()


@pytest.mark.asyncio
async def test_start_unknown_user_not_in_env_creates_pending(monkeypatch, mock_message):
    """Regression: unknown users not in env still get the original pending flow."""
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "555")  # different from user
    from bot.users import get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()

    from bot.routers.onboarding import cmd_start

    msg = mock_message(chat_id=777, user_id=777)
    msg.from_user.full_name = "Stranger"
    msg.from_user.username = "stranger"

    create_pending_mock = MagicMock()

    with patch("bot.routers.onboarding.is_admin", return_value=False), \
         patch("bot.routers.onboarding.get_user_role", return_value="unknown"), \
         patch("bot.routers.onboarding.create_pending_user", create_pending_mock), \
         patch("bot.routers.onboarding.get_bot", return_value=AsyncMock()):
        await cmd_start(msg)

    create_pending_mock.assert_called_once()
```

- [ ] **Step 10.2: Run tests to verify they fail**

```bash
cd /Users/bigode/Dev/agentics_workflows
pytest tests/test_onboarding_approver.py -v
```

Expected: at least the first test (`test_start_approver_only_does_not_create_pending`) FAILS — current code creates pending user for any unknown chat.

- [ ] **Step 10.3: Modify `cmd_start` in `webhook/bot/routers/onboarding.py`**

Find the existing function (lines 32–83). The change is to insert the approver-only check between the `pending` branch and the unknown-user creation branch.

Replace the entire `cmd_start` function with:

```python
@onboarding_router.message(Command("start"))
async def cmd_start(message: Message):
    chat_id = message.chat.id
    role = get_user_role(chat_id)

    if role == "admin":
        await message.answer(
            "🥸 *SuperMustache BOT*\n\nBem vindo, admin.",
            reply_markup=build_reply_keyboard(is_admin=True),
        )
        return

    if role == "subscriber":
        await message.answer(
            "🥸 *SuperMustache BOT*\n\nBem vindo de volta!",
            reply_markup=build_reply_keyboard(),
        )
        return

    if role == "pending":
        await message.answer(
            "⏳ Seu pedido de acesso ainda esta em analise.\n"
            "Voce recebera uma notificacao quando aprovado.",
        )
        return

    # OneDrive approver who isn't admin/subscriber/pending — fixed welcome,
    # no pending record created. They interact only via approval card buttons.
    if is_onedrive_approver(chat_id) and not is_admin(chat_id) and get_user(chat_id) is None:
        await message.answer(
            "👋 Olá! Você está cadastrado como aprovador de relatórios "
            "OneDrive.\n\nEu vou te enviar os PDFs novos da pasta SharePoint "
            "assim que chegarem. Use os botões de cada card pra aprovar ou "
            "descartar.",
        )
        return

    # Unknown user — create pending + notify admin (existing behavior)
    user = message.from_user
    name = user.full_name or "Desconhecido"
    username = user.username or ""
    create_pending_user(chat_id=chat_id, name=name, username=username)

    await message.answer(
        "Ola! Este bot e restrito.\n\n"
        "Seu pedido de acesso foi enviado ao administrador.\n"
        "Voce recebera uma notificacao quando aprovado.",
    )

    bot = get_bot()
    admin_id = int(TELEGRAM_CHAT_ID) if TELEGRAM_CHAT_ID.isdigit() else 0
    if admin_id:
        mention = f"@{username}" if username else name
        await bot.send_message(
            admin_id,
            f"🔔 *Novo pedido de acesso*\n\n"
            f"Nome: {name}\n"
            f"User: {mention}\n"
            f"ID: `{chat_id}`",
            reply_markup=build_approval_request_keyboard(chat_id),
        )
    logger.info(f"Access request from {chat_id} ({name})")
```

Add the new import at the top of the file (line ~22, alongside the existing `from bot.users import ...`):

```python
from bot.users import (
    get_user, create_pending_user, approve_user, reject_user,
    get_user_role, is_admin, is_onedrive_approver, toggle_subscription,
)
```

(Add `is_onedrive_approver` to the existing import list.)

- [ ] **Step 10.4: Run tests to verify they pass**

```bash
cd /Users/bigode/Dev/agentics_workflows
pytest tests/test_onboarding_approver.py -v
```

Expected: all PASS.

- [ ] **Step 10.5: Run the full test suite to check for regressions**

```bash
cd /Users/bigode/Dev/agentics_workflows
pytest tests/test_onedrive_pipeline.py tests/test_onedrive_callbacks.py tests/test_onedrive_route.py tests/test_onedrive_resubscribe.py tests/test_users_onedrive_approver.py tests/test_onboarding_approver.py -v
```

Expected: all PASS.

- [ ] **Step 10.6: Commit**

```bash
git add webhook/bot/routers/onboarding.py tests/test_onboarding_approver.py
git commit -m "feat(onboarding): /start gives approver-only users a fixed welcome (no pending record)"
```

---

## Task 11: Final integration verification

**No code changes** — this is a verification gate before deploy.

- [ ] **Step 11.1: Run the full repo test suite**

```bash
cd /Users/bigode/Dev/agentics_workflows
pytest tests/ -v --tb=short
```

Expected: all green. Investigate and fix any failures before deploy. Note: test files unrelated to OneDrive (e.g., contacts, baltic, queue) should not have been affected by these changes — but if any fail, that's a regression to debug.

- [ ] **Step 11.2: Boot the bot locally and smoke-test imports**

```bash
cd /Users/bigode/Dev/agentics_workflows
python -c "from webhook.bot.main import create_app; app = create_app(); print('app created with', len(app.router.routes()), 'routes')"
```

Expected: prints route count without ImportError, AttributeError, or aiogram registration errors. If errors, the most likely cause is a missing import or a typo in the new `F.func` filter syntax.

- [ ] **Step 11.3: Static smoke-check the cascade text strings**

```bash
cd /Users/bigode/Dev/agentics_workflows
grep -n "Sendo decidido\|Decidido por\|Descartado por" webhook/bot/routers/callbacks_onedrive.py
```

Expected: each cascade message string appears exactly once in the source.

- [ ] **Step 11.4: Verify env example documentation**

```bash
cd /Users/bigode/Dev/agentics_workflows
grep -A 6 "ONEDRIVE_APPROVER_IDS" .env.example
```

Expected: shows the documentation block + empty assignment.

- [ ] **Step 11.5: Confirm `git status` is clean and history is sensible**

```bash
git status
git log --oneline -12
```

Expected: working tree clean. Last 10 commits should be the implementation tasks (1 per task), in order, with conventional commit messages.

---

## Pre-deploy checklist (manual — do before pushing to Railway)

These are not automated but are part of the spec's "E2E manual checklist (pre-deploy)" — run them once locally or in a Railway preview env before merging to main.

- [ ] Set `ONEDRIVE_APPROVER_IDS=<colleague_chat_id>` in Railway env vars.
- [ ] Confirm the colleague has started a chat with the bot at least once (otherwise `bot.send_message` will fail with `chat not found`).
- [ ] Confirm a `smoke_test` `contact_lists` row exists in Supabase with only your own WhatsApp number.

Then walk through Scenarios 1–7 from the spec's E2E checklist (`docs/superpowers/specs/2026-04-27-onedrive-multi-approver-design.md` § Testing Strategy → E2E manual checklist):

- [ ] Scenario 1: admin clicks first → colleague's card freezes to 🔒, then to ✏️ Decidido por … ✅
- [ ] Scenario 2: colleague clicks first → admin's card mirrors that flow
- [ ] Scenario 3: discard → both cards show ❌ with attribution
- [ ] Scenario 4: simultaneous race → toast on the loser
- [ ] Scenario 5: env empty → admin-only behavior preserved (regression)
- [ ] Scenario 6: colleague blocks the bot → admin still works, log shows partial-fanout warning
- [ ] Scenario 7: Voltar does not destrava — colleague's card stays 🔒

Tail `event_log` in Supabase during smoke test to verify `trace_id` correlation:

```sql
SELECT created_at, event, label, detail
FROM event_log
WHERE workflow = 'onedrive_webhook'
  AND created_at > now() - interval '5 minutes'
ORDER BY created_at;
```

Expected sequence under a single `trace_id`: `webhook_received → delta_query_done → approval_created → approval_fanout → approval_claimed → approval_clicked → approval_approved → cascade_edit_skipped/done → dispatch_started → pdf_downloaded → dispatch_completed`.

---

## Rollback plan

Each task is one commit. If smoke test reveals a problem, revert in reverse order:

```bash
# Revert just the most recent task's commit:
git revert HEAD --no-edit
git push

# Or revert a specific task:
git revert <commit-sha> --no-edit
git push
```

Most surgical rollback: set `ONEDRIVE_APPROVER_IDS=` (empty) in Railway env. This restores admin-only fan-out behavior without code revert. Code paths for `_claim` / `_edit_others` still execute but with `recipients=[admin]` the cascade is a no-op.
