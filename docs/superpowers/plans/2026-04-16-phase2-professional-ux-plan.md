# Phase 2: Professional UX + Subscriptions + Telegram Delivery — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add invite-based access with admin approval, subscription-based Telegram delivery to approved users, persistent reply keyboard navigation, and professional onboarding — transforming the bot from single-admin tool to multi-user platform.

**Architecture:** New `webhook/bot/users.py` provides Redis-backed user CRUD (role, status, subscriptions). Auth middleware evolves from binary admin check to role-aware (admin/subscriber/unknown). `/start` becomes the entry point for access requests + onboarding. New `webhook/bot/delivery.py` sends workflow content to subscribed Telegram users. Execution scripts gain `workflow_type` + `direct_delivery` fields in `/store-draft` payloads.

**Tech Stack:** Aiogram 3 (FSM, CallbackData, middleware), Redis (user store), aiohttp (routes), Python 3.9+

---

## File Map

### New files (create)

| File | Responsibility |
|------|---------------|
| `webhook/bot/users.py` | User CRUD: create, get, approve, reject, list subscribers, subscription toggle |
| `webhook/bot/delivery.py` | Telegram delivery to subscribed users |
| `webhook/bot/routers/onboarding.py` | /start flow (access request for unknown, onboarding for approved), admin approval callbacks |
| `webhook/bot/routers/settings.py` | /settings, /menu, subscription management panel |
| `tests/test_bot_users.py` | User CRUD unit tests |
| `tests/test_bot_delivery.py` | Telegram delivery unit tests |
| `tests/test_bot_onboarding.py` | Onboarding flow tests |
| `tests/test_bot_settings.py` | Subscription toggle tests |

### Modified files

| File | Changes |
|------|---------|
| `webhook/bot/callback_data.py` | Add `UserApproval`, `SubscriptionToggle`, `SubscriptionDone`, `OnboardingStart` |
| `webhook/bot/keyboards.py` | Add `build_reply_keyboard()`, `build_subscription_keyboard()`, `build_approval_request_keyboard()`, `build_onboarding_keyboard()` |
| `webhook/bot/middlewares/auth.py` | Replace `AdminAuthMiddleware` with role-aware `RoleMiddleware` accepting allowed roles |
| `webhook/bot/routers/commands.py` | Remove old `/start`, update admin router to use new middleware, add `/settings`, `/menu` |
| `webhook/bot/routers/callbacks.py` | Update middleware to `RoleMiddleware`, keep all existing handlers |
| `webhook/bot/routers/messages.py` | Update middleware to `RoleMiddleware` |
| `webhook/bot/main.py` | Register onboarding + settings routers |
| `webhook/routes/api.py` | Update `/store-draft` to accept `workflow_type` + `direct_delivery`, trigger Telegram delivery |
| `webhook/bot/config.py` | Add `ADMIN_CHAT_ID` constant (parsed from `TELEGRAM_CHAT_ID`) |
| `execution/curation/rationale_dispatcher.py` | Add `workflow_type` + `direct_delivery` to `/store-draft` payload |

---

## Task 1: User Store (Redis CRUD)

**Files:**
- Create: `webhook/bot/users.py`
- Test: `tests/test_bot_users.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_bot_users.py`:

```python
"""Tests for webhook/bot/users.py — Redis-backed user store."""
import sys
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

import fakeredis
import pytest


@pytest.fixture
def fake_redis():
    client = fakeredis.FakeRedis(decode_responses=True)
    with patch("bot.users._get_client", return_value=client):
        yield client


def test_create_pending_user(fake_redis):
    from bot.users import create_pending_user, get_user
    create_pending_user(chat_id=111, name="Joao", username="joaosilva")
    user = get_user(111)
    assert user is not None
    assert user["chat_id"] == 111
    assert user["name"] == "Joao"
    assert user["username"] == "joaosilva"
    assert user["role"] == "subscriber"
    assert user["status"] == "pending"
    assert all(user["subscriptions"][k] is True for k in user["subscriptions"])


def test_get_user_not_found(fake_redis):
    from bot.users import get_user
    assert get_user(999) is None


def test_approve_user(fake_redis):
    from bot.users import create_pending_user, approve_user, get_user
    create_pending_user(chat_id=222, name="Maria", username="maria")
    approve_user(222)
    user = get_user(222)
    assert user["status"] == "approved"
    assert user["approved_at"] is not None


def test_reject_user(fake_redis):
    from bot.users import create_pending_user, reject_user, get_user
    create_pending_user(chat_id=333, name="Pedro", username="pedro")
    reject_user(333)
    user = get_user(333)
    assert user["status"] == "rejected"


def test_get_subscribers_for_workflow(fake_redis):
    from bot.users import create_pending_user, approve_user, get_subscribers_for_workflow, toggle_subscription
    create_pending_user(chat_id=100, name="A", username="a")
    create_pending_user(chat_id=200, name="B", username="b")
    approve_user(100)
    approve_user(200)
    toggle_subscription(200, "morning_check")  # turn OFF
    subs = get_subscribers_for_workflow("morning_check")
    assert len(subs) == 1
    assert subs[0]["chat_id"] == 100


def test_toggle_subscription(fake_redis):
    from bot.users import create_pending_user, get_user, toggle_subscription
    create_pending_user(chat_id=444, name="Ana", username="ana")
    new_val = toggle_subscription(444, "morning_check")
    assert new_val is False  # was True, now False
    user = get_user(444)
    assert user["subscriptions"]["morning_check"] is False
    new_val = toggle_subscription(444, "morning_check")
    assert new_val is True  # toggled back


def test_is_admin(fake_redis):
    from bot.users import is_admin
    with patch("bot.users.ADMIN_CHAT_ID", 111):
        assert is_admin(111) is True
        assert is_admin(999) is False


def test_get_user_role(fake_redis):
    from bot.users import create_pending_user, approve_user, get_user_role
    assert get_user_role(999) == "unknown"
    create_pending_user(chat_id=555, name="X", username="x")
    assert get_user_role(555) == "pending"
    approve_user(555)
    assert get_user_role(555) == "subscriber"


def test_admin_role_from_env(fake_redis):
    from bot.users import get_user_role
    with patch("bot.users.ADMIN_CHAT_ID", 777):
        assert get_user_role(777) == "admin"


def test_list_pending_users(fake_redis):
    from bot.users import create_pending_user, list_pending_users
    create_pending_user(chat_id=10, name="P1", username="p1")
    create_pending_user(chat_id=20, name="P2", username="p2")
    pending = list_pending_users()
    assert len(pending) == 2
    ids = {u["chat_id"] for u in pending}
    assert ids == {10, 20}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source "/Users/bigode/Dev/Antigravity WF /.venv/bin/activate" && cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_bot_users.py -v`
Expected: FAIL (ModuleNotFoundError: No module named 'bot.users')

- [ ] **Step 3: Create webhook/bot/users.py**

```python
"""User store: Redis-backed CRUD for Telegram bot users.

Redis key pattern: user:{chat_id} -> JSON
No TTL — user records are persistent.

Roles: admin (from TELEGRAM_CHAT_ID env), subscriber (approved users)
Status: pending, approved, rejected
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from bot.config import TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

ADMIN_CHAT_ID = int(TELEGRAM_CHAT_ID) if TELEGRAM_CHAT_ID.isdigit() else 0

_USER_KEY_PREFIX = "user:"

DEFAULT_SUBSCRIPTIONS = {
    "morning_check": True,
    "baltic_ingestion": True,
    "daily_report": True,
    "market_news": True,
    "platts_reports": True,
}


def _get_client():
    """Return Redis client (same as curation keyspace)."""
    from execution.curation.redis_client import _get_client as _rc
    return _rc()


def _user_key(chat_id: int) -> str:
    return f"{_USER_KEY_PREFIX}{chat_id}"


def get_user(chat_id: int) -> Optional[dict]:
    """Return user dict or None."""
    try:
        raw = _get_client().get(_user_key(chat_id))
        if raw:
            return json.loads(raw)
    except Exception as exc:
        logger.warning(f"get_user({chat_id}) failed: {exc}")
    return None


def _save_user(user: dict) -> None:
    """Persist user dict to Redis (no TTL)."""
    try:
        _get_client().set(_user_key(user["chat_id"]), json.dumps(user))
    except Exception as exc:
        logger.error(f"_save_user({user.get('chat_id')}) failed: {exc}")


def create_pending_user(chat_id: int, name: str, username: str) -> dict:
    """Create a new user with status=pending and all subscriptions ON."""
    user = {
        "chat_id": chat_id,
        "name": name,
        "username": username or "",
        "role": "subscriber",
        "status": "pending",
        "subscriptions": dict(DEFAULT_SUBSCRIPTIONS),
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "approved_at": None,
    }
    _save_user(user)
    return user


def approve_user(chat_id: int) -> Optional[dict]:
    """Set user status to approved. Returns updated user or None."""
    user = get_user(chat_id)
    if user is None:
        return None
    updated = {
        **user,
        "status": "approved",
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_user(updated)
    return updated


def reject_user(chat_id: int) -> Optional[dict]:
    """Set user status to rejected. Returns updated user or None."""
    user = get_user(chat_id)
    if user is None:
        return None
    updated = {**user, "status": "rejected"}
    _save_user(updated)
    return updated


def toggle_subscription(chat_id: int, workflow: str) -> bool:
    """Toggle a subscription on/off. Returns new value."""
    user = get_user(chat_id)
    if user is None:
        return False
    subs = user.get("subscriptions", {})
    current = subs.get(workflow, True)
    subs[workflow] = not current
    updated = {**user, "subscriptions": subs}
    _save_user(updated)
    return not current


def get_subscribers_for_workflow(workflow: str) -> list[dict]:
    """Return all approved users subscribed to a given workflow."""
    client = _get_client()
    users = []
    for key in client.scan_iter(match=f"{_USER_KEY_PREFIX}*", count=200):
        raw = client.get(key)
        if not raw:
            continue
        try:
            user = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if user.get("status") != "approved":
            continue
        if user.get("subscriptions", {}).get(workflow, False):
            users.append(user)
    return users


def list_pending_users() -> list[dict]:
    """Return all users with status=pending."""
    client = _get_client()
    pending = []
    for key in client.scan_iter(match=f"{_USER_KEY_PREFIX}*", count=200):
        raw = client.get(key)
        if not raw:
            continue
        try:
            user = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if user.get("status") == "pending":
            pending.append(user)
    return pending


def is_admin(chat_id: int) -> bool:
    """Check if chat_id is the admin."""
    return chat_id == ADMIN_CHAT_ID and ADMIN_CHAT_ID != 0


def get_user_role(chat_id: int) -> str:
    """Return role string: 'admin', 'subscriber', 'pending', or 'unknown'."""
    if is_admin(chat_id):
        return "admin"
    user = get_user(chat_id)
    if user is None:
        return "unknown"
    status = user.get("status", "")
    if status == "approved":
        return "subscriber"
    if status == "pending":
        return "pending"
    return "unknown"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source "/Users/bigode/Dev/Antigravity WF /.venv/bin/activate" && cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_bot_users.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add webhook/bot/users.py tests/test_bot_users.py
git commit -m "feat(bot): add Redis-backed user store with roles and subscriptions"
```

---

## Task 2: New CallbackData Factories

**Files:**
- Modify: `webhook/bot/callback_data.py`

- [ ] **Step 1: Add new CallbackData classes**

Add to the end of `webhook/bot/callback_data.py`:

```python
class UserApproval(CallbackData, prefix="user_approve"):
    action: str  # approve, reject
    chat_id: int


class SubscriptionToggle(CallbackData, prefix="sub_toggle"):
    workflow: str  # morning_check, baltic_ingestion, etc.


class SubscriptionDone(CallbackData, prefix="sub_done"):
    pass


class OnboardingStart(CallbackData, prefix="onboard"):
    pass
```

- [ ] **Step 2: Add tests for new factories**

Append to `tests/test_bot_callback_data.py`:

```python
from bot.callback_data import UserApproval, SubscriptionToggle, SubscriptionDone, OnboardingStart


def test_user_approval_pack_unpack():
    cb = UserApproval(action="approve", chat_id=12345)
    packed = cb.pack()
    assert packed.startswith("user_approve:")
    parsed = UserApproval.unpack(packed)
    assert parsed.action == "approve"
    assert parsed.chat_id == 12345


def test_subscription_toggle_pack_unpack():
    cb = SubscriptionToggle(workflow="morning_check")
    packed = cb.pack()
    parsed = SubscriptionToggle.unpack(packed)
    assert parsed.workflow == "morning_check"


def test_subscription_done_pack_unpack():
    cb = SubscriptionDone()
    packed = cb.pack()
    parsed = SubscriptionDone.unpack(packed)
    assert parsed is not None


def test_onboarding_start_pack_unpack():
    cb = OnboardingStart()
    packed = cb.pack()
    parsed = OnboardingStart.unpack(packed)
    assert parsed is not None
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_bot_callback_data.py -v`
Expected: All pass (original 16 + 4 new = 20)

- [ ] **Step 4: Commit**

```bash
git add webhook/bot/callback_data.py tests/test_bot_callback_data.py
git commit -m "feat(bot): add UserApproval, SubscriptionToggle, SubscriptionDone CallbackData"
```

---

## Task 3: Keyboard Builders (Reply + Subscription + Approval)

**Files:**
- Modify: `webhook/bot/keyboards.py`

- [ ] **Step 1: Add new keyboard functions**

Add these imports and functions to `webhook/bot/keyboards.py`:

At the top, add to imports:

```python
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from bot.callback_data import (
    DraftAction, MenuAction,
    ReportType as ReportTypeCB, ReportYears, ReportBack,
    WorkflowList,
    UserApproval, SubscriptionToggle, SubscriptionDone, OnboardingStart,
)
```

(Replace the existing `from bot.callback_data import` line.)

Then add these new functions after the existing ones:

```python
def build_reply_keyboard() -> ReplyKeyboardMarkup:
    """Build the persistent 2x2 reply keyboard for bottom navigation."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Reports"), KeyboardButton(text="📰 Fila")],
            [KeyboardButton(text="⚡ Workflows"), KeyboardButton(text="⚙️ Settings")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def build_onboarding_keyboard() -> InlineKeyboardMarkup:
    """Build the onboarding welcome keyboard."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="⚡ Configurar notificacoes",
        callback_data=OnboardingStart().pack(),
    ))
    return builder.as_markup()


def build_subscription_keyboard(subscriptions: dict) -> InlineKeyboardMarkup:
    """Build the subscription toggle panel.

    subscriptions: dict like {"morning_check": True, "baltic_ingestion": False, ...}
    """
    labels = {
        "morning_check": "Morning Check — Precos Platts",
        "baltic_ingestion": "Baltic Exchange — BDI + Rotas",
        "daily_report": "Daily SGX — Futuros 62% Fe",
        "market_news": "Platts News — Noticias curadas",
        "platts_reports": "Platts Reports — PDFs",
    }
    builder = InlineKeyboardBuilder()
    for wf, label in labels.items():
        active = subscriptions.get(wf, True)
        icon = "✅" if active else "❌"
        builder.row(InlineKeyboardButton(
            text=f"{icon} {label}",
            callback_data=SubscriptionToggle(workflow=wf).pack(),
        ))
    builder.row(InlineKeyboardButton(
        text="💾 Pronto",
        callback_data=SubscriptionDone().pack(),
    ))
    return builder.as_markup()


def build_approval_request_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    """Build the admin approval request keyboard."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Aprovar",
            callback_data=UserApproval(action="approve", chat_id=chat_id).pack(),
        ),
        InlineKeyboardButton(
            text="❌ Recusar",
            callback_data=UserApproval(action="reject", chat_id=chat_id).pack(),
        ),
    )
    return builder.as_markup()
```

- [ ] **Step 2: Verify imports work**

Run: `source "/Users/bigode/Dev/Antigravity WF /.venv/bin/activate" && cd "/Users/bigode/Dev/Antigravity WF " && python3 -c "import sys; sys.path.insert(0, 'webhook'); from bot.keyboards import build_reply_keyboard, build_subscription_keyboard, build_approval_request_keyboard, build_onboarding_keyboard; rk = build_reply_keyboard(); print(f'Reply KB rows: {len(rk.keyboard)}'); sk = build_subscription_keyboard({'morning_check': True, 'baltic_ingestion': False, 'daily_report': True, 'market_news': True, 'platts_reports': True}); print(f'Sub KB rows: {len(sk.inline_keyboard)}')"`

Expected: Reply KB rows: 2, Sub KB rows: 6

- [ ] **Step 3: Commit**

```bash
git add webhook/bot/keyboards.py
git commit -m "feat(bot): add reply keyboard, subscription panel, approval request keyboards"
```

---

## Task 4: Role-Aware Middleware

**Files:**
- Modify: `webhook/bot/middlewares/auth.py`
- Test: `tests/test_bot_middlewares.py`

- [ ] **Step 1: Update tests**

Replace `tests/test_bot_middlewares.py`:

```python
"""Tests for role-aware middleware."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

import pytest
from bot.middlewares.auth import RoleMiddleware


def _make_event(user_id):
    event = MagicMock()
    event.from_user = MagicMock()
    event.from_user.id = user_id
    return event


@pytest.mark.asyncio
async def test_admin_passes_admin_only_middleware():
    mw = RoleMiddleware(allowed_roles={"admin"})
    handler = AsyncMock(return_value="result")
    event = _make_event(12345)
    with patch("bot.middlewares.auth.get_user_role", return_value="admin"):
        result = await mw(handler, event, {})
    assert result == "result"
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_subscriber_blocked_by_admin_only():
    mw = RoleMiddleware(allowed_roles={"admin"})
    handler = AsyncMock(return_value="result")
    event = _make_event(99999)
    with patch("bot.middlewares.auth.get_user_role", return_value="subscriber"):
        result = await mw(handler, event, {})
    assert result is None
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_subscriber_passes_subscriber_middleware():
    mw = RoleMiddleware(allowed_roles={"admin", "subscriber"})
    handler = AsyncMock(return_value="result")
    event = _make_event(55555)
    with patch("bot.middlewares.auth.get_user_role", return_value="subscriber"):
        result = await mw(handler, event, {})
    assert result == "result"
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_user_blocked():
    mw = RoleMiddleware(allowed_roles={"admin", "subscriber"})
    handler = AsyncMock(return_value="result")
    event = _make_event(77777)
    with patch("bot.middlewares.auth.get_user_role", return_value="unknown"):
        result = await mw(handler, event, {})
    assert result is None
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_event_without_from_user_passes():
    mw = RoleMiddleware(allowed_roles={"admin"})
    handler = AsyncMock(return_value="result")
    event = MagicMock(spec=[])  # no from_user attr
    result = await mw(handler, event, {})
    assert result == "result"
    handler.assert_awaited_once()
```

- [ ] **Step 2: Run to verify tests fail**

Run: `pytest tests/test_bot_middlewares.py -v`
Expected: FAIL (ImportError: cannot import name 'RoleMiddleware')

- [ ] **Step 3: Rewrite webhook/bot/middlewares/auth.py**

```python
"""Role-aware authorization middleware.

Replaces the binary AdminAuthMiddleware with a configurable RoleMiddleware
that accepts a set of allowed roles. Uses bot.users.get_user_role() to
determine the user's role (admin, subscriber, pending, unknown).

Usage:
  admin_router.message.middleware(RoleMiddleware(allowed_roles={"admin"}))
  shared_router.message.middleware(RoleMiddleware(allowed_roles={"admin", "subscriber"}))
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Set

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from bot.users import get_user_role

logger = logging.getLogger(__name__)


class RoleMiddleware(BaseMiddleware):
    def __init__(self, allowed_roles: Set[str]):
        self.allowed_roles = allowed_roles
        super().__init__()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        from_user = getattr(event, "from_user", None)
        if from_user is None:
            return await handler(event, data)

        role = get_user_role(from_user.id)
        if role not in self.allowed_roles:
            logger.debug(f"Role '{role}' not in {self.allowed_roles} for chat_id={from_user.id}")
            return None

        data["user_role"] = role
        return await handler(event, data)


# Backward compat alias used by existing routers
AdminAuthMiddleware = lambda: RoleMiddleware(allowed_roles={"admin"})
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_bot_middlewares.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add webhook/bot/middlewares/auth.py tests/test_bot_middlewares.py
git commit -m "feat(bot): replace AdminAuthMiddleware with role-aware RoleMiddleware"
```

---

## Task 5: Update Existing Routers for Role Middleware

**Files:**
- Modify: `webhook/bot/routers/commands.py`
- Modify: `webhook/bot/routers/callbacks.py`
- Modify: `webhook/bot/routers/messages.py`

- [ ] **Step 1: Update commands.py middleware**

In `webhook/bot/routers/commands.py`, change:

```python
from bot.middlewares.auth import AdminAuthMiddleware
```
to:
```python
from bot.middlewares.auth import RoleMiddleware
```

And change:
```python
admin_router.message.middleware(AdminAuthMiddleware())
```
to:
```python
admin_router.message.middleware(RoleMiddleware(allowed_roles={"admin"}))
```

Also add a shared router for commands available to both admin and subscribers. Add after the admin_router definition:

```python
# ── Shared router (admin + subscriber) ──

shared_router = Router(name="commands_shared")
shared_router.message.middleware(RoleMiddleware(allowed_roles={"admin", "subscriber"}))


@shared_router.message(Command("settings"))
async def cmd_settings(message: Message):
    from bot.routers.settings import show_subscription_panel
    await show_subscription_panel(message.chat.id)


@shared_router.message(Command("menu"))
async def cmd_menu_reply(message: Message):
    from bot.keyboards import build_reply_keyboard
    await message.answer("🥸 *SuperMustache BOT*", reply_markup=build_reply_keyboard())
```

And remove the old `/start` handler from `public_router` (it will be in `onboarding.py`).

- [ ] **Step 2: Update callbacks.py middleware**

In `webhook/bot/routers/callbacks.py`, change:

```python
from bot.middlewares.auth import AdminAuthMiddleware
```
to:
```python
from bot.middlewares.auth import RoleMiddleware
```

And change:
```python
callback_router.callback_query.middleware(AdminAuthMiddleware())
```
to:
```python
callback_router.callback_query.middleware(RoleMiddleware(allowed_roles={"admin"}))
```

- [ ] **Step 3: Update messages.py middleware**

In `webhook/bot/routers/messages.py`, change:

```python
from bot.middlewares.auth import AdminAuthMiddleware
```
to:
```python
from bot.middlewares.auth import RoleMiddleware
```

And change:
```python
message_router.message.middleware(AdminAuthMiddleware())
```
to:
```python
message_router.message.middleware(RoleMiddleware(allowed_roles={"admin"}))
```

- [ ] **Step 4: Run existing tests**

Run: `pytest tests/ -v --tb=short -q`
Expected: All pass (320+)

- [ ] **Step 5: Commit**

```bash
git add webhook/bot/routers/commands.py webhook/bot/routers/callbacks.py webhook/bot/routers/messages.py
git commit -m "refactor(bot): migrate routers from AdminAuthMiddleware to RoleMiddleware"
```

---

## Task 6: Onboarding Router (Access Request + Approval)

**Files:**
- Create: `webhook/bot/routers/onboarding.py`

- [ ] **Step 1: Create webhook/bot/routers/onboarding.py**

```python
"""Onboarding flow: /start for unknown users, admin approval, welcome wizard.

Public router (no middleware) — handles all /start regardless of role.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

from bot.config import get_bot, TELEGRAM_CHAT_ID
from bot.callback_data import UserApproval, OnboardingStart, SubscriptionToggle, SubscriptionDone
from bot.keyboards import (
    build_reply_keyboard, build_approval_request_keyboard,
    build_onboarding_keyboard, build_subscription_keyboard,
)
from bot.users import (
    get_user, create_pending_user, approve_user, reject_user,
    get_user_role, is_admin, toggle_subscription,
)

logger = logging.getLogger(__name__)

onboarding_router = Router(name="onboarding")


# ── /start command (public, no middleware) ──

@onboarding_router.message(Command("start"))
async def cmd_start(message: Message):
    chat_id = message.chat.id
    role = get_user_role(chat_id)

    if role == "admin":
        await message.answer(
            "🥸 *SuperMustache BOT*\n\nBem vindo, admin.",
            reply_markup=build_reply_keyboard(),
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

    # Unknown user — create pending + notify admin
    user = message.from_user
    name = user.full_name or "Desconhecido"
    username = user.username or ""
    create_pending_user(chat_id=chat_id, name=name, username=username)

    await message.answer(
        "Ola! Este bot e restrito.\n\n"
        "Seu pedido de acesso foi enviado ao administrador.\n"
        "Voce recebera uma notificacao quando aprovado.",
    )

    # Notify admin
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


# ── Admin approval/rejection callbacks ──

@onboarding_router.callback_query(UserApproval.filter())
async def on_user_approval(query: CallbackQuery, callback_data: UserApproval):
    requester_id = query.from_user.id
    if not is_admin(requester_id):
        await query.answer("Nao autorizado")
        return

    target_chat_id = callback_data.chat_id
    action = callback_data.action
    bot = get_bot()

    if action == "approve":
        user = approve_user(target_chat_id)
        if user is None:
            await query.answer("Usuario nao encontrado")
            return
        await query.answer("✅ Aprovado")

        # Update admin message
        await bot.edit_message_text(
            f"✅ *Aprovado* — {user['name']}",
            chat_id=query.message.chat.id,
            message_id=query.message.message_id,
            reply_markup=None,
        )

        # Send onboarding to the approved user
        await bot.send_message(
            target_chat_id,
            "🥸 *SuperMustache BOT*\n\n"
            "Iron ore market intelligence direto no seu Telegram.\n\n"
            "O que voce vai receber:\n"
            "• Precos Platts em tempo real\n"
            "• Noticias curadas por IA\n"
            "• Baltic Exchange (BDI + rotas)\n"
            "• Futuros SGX 62% Fe\n"
            "• Reports PDF Platts\n\n"
            "Vamos configurar o que te interessa?",
            reply_markup=build_onboarding_keyboard(),
        )
        logger.info(f"User {target_chat_id} approved")

    elif action == "reject":
        user = reject_user(target_chat_id)
        if user is None:
            await query.answer("Usuario nao encontrado")
            return
        await query.answer("❌ Recusado")

        await bot.edit_message_text(
            f"❌ *Recusado* — {user['name']}",
            chat_id=query.message.chat.id,
            message_id=query.message.message_id,
            reply_markup=None,
        )

        await bot.send_message(target_chat_id, "Acesso nao autorizado.")
        logger.info(f"User {target_chat_id} rejected")


# ── Onboarding: "Configurar notificacoes" button ──

@onboarding_router.callback_query(OnboardingStart.filter())
async def on_onboarding_start(query: CallbackQuery):
    user = get_user(query.from_user.id)
    if user is None:
        await query.answer("Erro")
        return
    subs = user.get("subscriptions", {})
    await query.answer("")
    bot = get_bot()
    await bot.edit_message_text(
        "⚙️ *Notificacoes*\n\n"
        "Escolha o que receber:\n\n"
        "Toque para ativar/desativar.",
        chat_id=query.message.chat.id,
        message_id=query.message.message_id,
        reply_markup=build_subscription_keyboard(subs),
    )


# ── Subscription toggle ──

@onboarding_router.callback_query(SubscriptionToggle.filter())
async def on_subscription_toggle(query: CallbackQuery, callback_data: SubscriptionToggle):
    chat_id = query.from_user.id
    new_val = toggle_subscription(chat_id, callback_data.workflow)
    user = get_user(chat_id)
    if user is None:
        await query.answer("Erro")
        return
    subs = user.get("subscriptions", {})
    icon = "✅" if new_val else "❌"
    await query.answer(f"{icon} {callback_data.workflow}")
    bot = get_bot()
    await bot.edit_message_reply_markup(
        chat_id=query.message.chat.id,
        message_id=query.message.message_id,
        reply_markup=build_subscription_keyboard(subs),
    )


# ── Subscription done ──

@onboarding_router.callback_query(SubscriptionDone.filter())
async def on_subscription_done(query: CallbackQuery):
    user = get_user(query.from_user.id)
    if user is None:
        await query.answer("Erro")
        return
    active = sum(1 for v in user.get("subscriptions", {}).values() if v)
    await query.answer("💾 Salvo!")
    bot = get_bot()
    await bot.edit_message_text(
        f"✅ *Configuracao salva*\n\n{active} notificacoes ativas.",
        chat_id=query.message.chat.id,
        message_id=query.message.message_id,
        reply_markup=None,
    )
    # Send reply keyboard
    await bot.send_message(
        query.from_user.id,
        "Use os botoes abaixo para navegar.",
        reply_markup=build_reply_keyboard(),
    )
```

- [ ] **Step 2: Commit**

```bash
git add webhook/bot/routers/onboarding.py
git commit -m "feat(bot): add onboarding router — access request, approval, subscription wizard"
```

---

## Task 7: Settings Router

**Files:**
- Create: `webhook/bot/routers/settings.py`

- [ ] **Step 1: Create webhook/bot/routers/settings.py**

```python
"""Settings and subscription management.

Handles the /settings command and reply keyboard "Settings" button.
Re-uses the same subscription panel from onboarding.
"""

from __future__ import annotations

import logging

from bot.config import get_bot
from bot.keyboards import build_subscription_keyboard
from bot.users import get_user

logger = logging.getLogger(__name__)


async def show_subscription_panel(chat_id: int, message_id: int = None):
    """Show the subscription toggle panel. If message_id given, edit; else send new."""
    bot = get_bot()
    user = get_user(chat_id)
    if user is None:
        await bot.send_message(chat_id, "⚠️ Voce nao esta registrado. Use /start.")
        return

    subs = user.get("subscriptions", {})
    text = "⚙️ *Notificacoes*\n\nEscolha o que receber:\n\nToque para ativar/desativar."

    if message_id:
        await bot.edit_message_text(
            text, chat_id=chat_id, message_id=message_id,
            reply_markup=build_subscription_keyboard(subs),
        )
    else:
        await bot.send_message(
            chat_id, text,
            reply_markup=build_subscription_keyboard(subs),
        )
```

- [ ] **Step 2: Commit**

```bash
git add webhook/bot/routers/settings.py
git commit -m "feat(bot): add settings module for subscription management"
```

---

## Task 8: Reply Keyboard Text Handler

**Files:**
- Modify: `webhook/bot/routers/messages.py`

- [ ] **Step 1: Add reply keyboard text handlers**

In `webhook/bot/routers/messages.py`, add a new router for reply keyboard text BEFORE the existing `message_router`. The reply keyboard sends plain text like "📊 Reports", which needs to be routed to the correct handler.

Add at the top of the file after imports:

```python
from bot.middlewares.auth import RoleMiddleware
from reports_nav import reports_show_types
```

Add a new router before the existing `message_router`:

```python
# ── Reply keyboard text handler (admin + subscriber) ──

reply_kb_router = Router(name="reply_keyboard")
reply_kb_router.message.middleware(RoleMiddleware(allowed_roles={"admin", "subscriber"}))


@reply_kb_router.message(F.text == "📊 Reports")
async def on_reply_reports(message: Message):
    await reports_show_types(message.chat.id)


@reply_kb_router.message(F.text == "📰 Fila")
async def on_reply_queue(message: Message):
    import query_handlers
    try:
        body, markup = query_handlers.format_queue_page(page=1)
    except Exception:
        await message.answer("❌ Erro ao consultar staging.")
        return
    await message.answer(body, reply_markup=markup)


@reply_kb_router.message(F.text == "⚡ Workflows")
async def on_reply_workflows(message: Message):
    from workflow_trigger import render_workflow_list
    wf_text, wf_markup = await render_workflow_list()
    await message.answer(wf_text, reply_markup=wf_markup)


@reply_kb_router.message(F.text.in_({"⚙️ Settings", "⚙\ufe0f Settings"}))
async def on_reply_settings(message: Message):
    from bot.routers.settings import show_subscription_panel
    await show_subscription_panel(message.chat.id)
```

- [ ] **Step 2: Commit**

```bash
git add webhook/bot/routers/messages.py
git commit -m "feat(bot): add reply keyboard text handlers for bottom navigation"
```

---

## Task 9: Telegram Delivery Module

**Files:**
- Create: `webhook/bot/delivery.py`
- Test: `tests/test_bot_delivery.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_bot_delivery.py`:

```python
"""Tests for Telegram delivery to subscribers."""
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

import fakeredis
import pytest


@pytest.fixture
def fake_redis():
    client = fakeredis.FakeRedis(decode_responses=True)
    with patch("bot.users._get_client", return_value=client):
        yield client


@pytest.fixture
def mock_bot():
    bot = AsyncMock()
    bot.send_message = AsyncMock()
    return bot


def _seed_users(fake_redis):
    """Create 2 approved users, one subscribed to morning_check, one not."""
    import json
    fake_redis.set("user:100", json.dumps({
        "chat_id": 100, "name": "A", "username": "a",
        "role": "subscriber", "status": "approved",
        "subscriptions": {"morning_check": True, "daily_report": True},
    }))
    fake_redis.set("user:200", json.dumps({
        "chat_id": 200, "name": "B", "username": "b",
        "role": "subscriber", "status": "approved",
        "subscriptions": {"morning_check": False, "daily_report": True},
    }))


@pytest.mark.asyncio
async def test_deliver_to_subscribers(fake_redis, mock_bot):
    _seed_users(fake_redis)
    with patch("bot.delivery.get_bot", return_value=mock_bot):
        from bot.delivery import deliver_to_subscribers
        results = await deliver_to_subscribers("morning_check", "Test message")
    assert results["sent"] == 1
    assert results["failed"] == 0
    mock_bot.send_message.assert_awaited_once_with(100, "Test message")


@pytest.mark.asyncio
async def test_deliver_to_all_subscribed(fake_redis, mock_bot):
    _seed_users(fake_redis)
    with patch("bot.delivery.get_bot", return_value=mock_bot):
        from bot.delivery import deliver_to_subscribers
        results = await deliver_to_subscribers("daily_report", "Daily msg")
    assert results["sent"] == 2


@pytest.mark.asyncio
async def test_deliver_no_subscribers(fake_redis, mock_bot):
    with patch("bot.delivery.get_bot", return_value=mock_bot):
        from bot.delivery import deliver_to_subscribers
        results = await deliver_to_subscribers("morning_check", "Nobody")
    assert results["sent"] == 0
    mock_bot.send_message.assert_not_awaited()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bot_delivery.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Create webhook/bot/delivery.py**

```python
"""Telegram delivery to subscribed users.

Sends workflow content to all approved users who have the matching
subscription enabled. Used by /store-draft when direct_delivery=true.
"""

from __future__ import annotations

import logging

from bot.config import get_bot
from bot.users import get_subscribers_for_workflow

logger = logging.getLogger(__name__)


async def deliver_to_subscribers(workflow_type: str, message: str) -> dict:
    """Send message to all subscribers of workflow_type.

    Returns {"sent": int, "failed": int, "errors": list[str]}
    """
    bot = get_bot()
    subscribers = get_subscribers_for_workflow(workflow_type)

    sent = 0
    failed = 0
    errors = []

    for user in subscribers:
        chat_id = user["chat_id"]
        try:
            await bot.send_message(chat_id, message)
            sent += 1
        except Exception as exc:
            failed += 1
            errors.append(f"{chat_id}: {str(exc)[:100]}")
            logger.warning(f"Telegram delivery failed for {chat_id}: {exc}")

    logger.info(f"Telegram delivery [{workflow_type}]: {sent} sent, {failed} failed")
    return {"sent": sent, "failed": failed, "errors": errors}
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_bot_delivery.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add webhook/bot/delivery.py tests/test_bot_delivery.py
git commit -m "feat(bot): add Telegram delivery to subscribed users"
```

---

## Task 10: Update /store-draft for Direct Delivery

**Files:**
- Modify: `webhook/routes/api.py`

- [ ] **Step 1: Update store_draft route**

In `webhook/routes/api.py`, modify the `store_draft` function to handle `workflow_type` + `direct_delivery`:

Replace the existing `store_draft` function with:

```python
@routes.post("/store-draft")
async def store_draft(request: web.Request) -> web.Response:
    data = await request.json()
    draft_id = data.get("draft_id")
    message = data.get("message")
    if not draft_id or not message:
        return web.json_response({"error": "Missing draft_id or message"}, status=400)

    workflow_type = data.get("workflow_type")
    direct_delivery = data.get("direct_delivery", False)

    draft = {
        "message": message,
        "status": "pending",
        "original_text": "",
        "uazapi_token": (data.get("uazapi_token") or "").strip() or None,
        "uazapi_url": (data.get("uazapi_url") or "").strip() or None,
        "workflow_type": workflow_type,
        "direct_delivery": direct_delivery,
    }
    drafts_set(draft_id, draft)

    if draft["uazapi_token"]:
        logger.info(f"Draft includes UAZAPI token: {draft['uazapi_token'][:8]}...")
    else:
        logger.info(f"Draft has no UAZAPI token, will use env var")

    logger.info(f"Draft stored: {draft_id} ({len(message)} chars, workflow={workflow_type}, direct={direct_delivery})")

    # Telegram delivery to subscribers (non-blocking)
    telegram_result = None
    if direct_delivery and workflow_type:
        from bot.delivery import deliver_to_subscribers
        try:
            telegram_result = await deliver_to_subscribers(workflow_type, message)
            logger.info(f"Telegram delivery: {telegram_result}")
        except Exception as exc:
            logger.error(f"Telegram delivery failed: {exc}")
            telegram_result = {"sent": 0, "failed": 0, "error": str(exc)}

    response = {"success": True, "draft_id": draft_id}
    if telegram_result:
        response["telegram_delivery"] = telegram_result
    return web.json_response(response)
```

- [ ] **Step 2: Commit**

```bash
git add webhook/routes/api.py
git commit -m "feat(routes): add workflow_type + direct_delivery to /store-draft with Telegram delivery"
```

---

## Task 11: Update Execution Script Payloads

**Files:**
- Modify: `execution/curation/rationale_dispatcher.py`

- [ ] **Step 1: Add workflow_type and direct_delivery to rationale_dispatcher.py**

In `execution/curation/rationale_dispatcher.py`, find the `/store-draft` POST payload (~line 101) and add the two new fields:

Change the json payload from:
```python
json={
    "draft_id": draft_obj["id"],
    "message": draft_text,
    "uazapi_token": os.getenv("UAZAPI_TOKEN", ""),
    "uazapi_url": os.getenv("UAZAPI_URL", "https://mineralstrading.uazapi.com"),
},
```
to:
```python
json={
    "draft_id": draft_obj["id"],
    "message": draft_text,
    "uazapi_token": os.getenv("UAZAPI_TOKEN", ""),
    "uazapi_url": os.getenv("UAZAPI_URL", "https://mineralstrading.uazapi.com"),
    "workflow_type": "rationale_news",
    "direct_delivery": False,
},
```

(Rationale news still needs admin approval, so `direct_delivery: False`.)

- [ ] **Step 2: Commit**

```bash
git add execution/curation/rationale_dispatcher.py
git commit -m "feat(execution): add workflow_type to rationale_dispatcher /store-draft payload"
```

---

## Task 12: Register New Routers in main.py

**Files:**
- Modify: `webhook/bot/main.py`

- [ ] **Step 1: Update main.py**

Add imports for the new routers:

```python
from bot.routers.onboarding import onboarding_router
from bot.routers.messages import reply_kb_router
```

In `create_app()`, update the router registration order. The onboarding router must be FIRST (it handles /start for all users). Reply keyboard router must be BEFORE message_router (which has a catch-all `F.text`):

```python
def create_app() -> web.Application:
    dp = get_dispatcher()
    dp.include_router(onboarding_router)   # /start + approval + subscription callbacks (public)
    dp.include_router(public_router)        # other public commands (none currently)
    dp.include_router(admin_router)         # admin-only commands
    dp.include_router(shared_router)        # /settings, /menu (admin + subscriber)
    dp.include_router(callback_router)      # all inline button callbacks (admin)
    dp.include_router(reply_kb_router)      # reply keyboard text (admin + subscriber)
    dp.include_router(message_router)       # FSM + catch-all text (admin)
    ...
```

Also add `shared_router` import:

```python
from bot.routers.commands import public_router, admin_router, shared_router
```

- [ ] **Step 2: Verify app creates**

Run: `source "/Users/bigode/Dev/Antigravity WF /.venv/bin/activate" && cd "/Users/bigode/Dev/Antigravity WF " && TELEGRAM_BOT_TOKEN="123456789:AAFakeTokenForTesting_abcdefghijk" REDIS_URL=redis://localhost:6379 TELEGRAM_CHAT_ID=12345 python3 -c "import sys; sys.path.insert(0, 'webhook'); from bot.main import create_app; app = create_app(); print('OK')"`

Expected: OK

- [ ] **Step 3: Commit**

```bash
git add webhook/bot/main.py
git commit -m "feat(bot): register onboarding, settings, and reply keyboard routers"
```

---

## Task 13: Integration Tests & Final Verification

- [ ] **Step 1: Run full test suite**

Run: `source "/Users/bigode/Dev/Antigravity WF /.venv/bin/activate" && cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/ -v --tb=short -q`
Expected: All pass (320+ original + new tests)

- [ ] **Step 2: Verify no regressions**

Run: `pytest tests/test_bot_states.py tests/test_bot_callback_data.py tests/test_bot_middlewares.py tests/test_bot_users.py tests/test_bot_delivery.py -v`
Expected: All new tests pass

- [ ] **Step 3: Verify app starts with all routes**

Run same verification as Task 12 Step 2.

- [ ] **Step 4: Fix any issues found**

If tests fail, fix the issues and commit.

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "feat: complete Phase 2 — professional UX, subscriptions, Telegram delivery"
```
