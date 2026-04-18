# Backend Hardening — Phase 2: Router Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decompose `webhook/bot/routers/callbacks.py` (601 lines, one god router) into 6 domain routers (curation, reports, queue, menu, contacts, workflows). Migrate stray reply-keyboard handlers from `messages.py` to `reply_kb_router`. Clean up inline imports. **Zero user-visible behavior change** — this is pure code motion.

**Architecture:** Each domain file exports its own `Router()` instance. `main.py` includes all 6 new routers in the order that preserves current filter precedence. The Phase 1 test safety net (53 characterization tests) is the guard — every step must keep the full suite green.

**Tech Stack:** Aiogram 3.4 `Router` instances. Python 3.9 compatible. No new dependencies.

**Spec reference:** `docs/superpowers/specs/2026-04-18-backend-hardening-v1-design.md` §3.

**Prerequisite:** Phase 1 must be merged (tag `phase1-safety-net-complete` on `main`). Verify with `git log phase1-safety-net-complete --oneline -1`.

---

## File Structure

**Create (6 new router files):**
- `webhook/bot/routers/callbacks_curation.py` — draft approve/reject/adjust, curate archive/reject/pipeline/send_raw, broadcast confirm; owns `_finalize_card` helper
- `webhook/bot/routers/callbacks_reports.py` — all report navigation handlers
- `webhook/bot/routers/callbacks_queue.py` — queue page + queue open handlers
- `webhook/bot/routers/callbacks_menu.py` — `on_menu_action` (main menu switchboard)
- `webhook/bot/routers/callbacks_contacts.py` — contact toggle + page
- `webhook/bot/routers/callbacks_workflows.py` — workflow run + list + nop

**Modify:**
- `webhook/bot/routers/callbacks.py` — progressively shrinks, then DELETED in Task 7
- `webhook/bot/main.py` — replace `callback_router` import with 6 new router imports; update `dp.include_router(...)` calls
- `webhook/bot/routers/messages.py` — migrate 7 `on_reply_*` handlers from `message_router` to `reply_kb_router` if needed (Task 8)
- `tests/test_callbacks_curation.py` — update handler imports to `bot.routers.callbacks_curation`
- `tests/test_callbacks_reports.py` — update imports to `bot.routers.callbacks_reports`
- `tests/test_callbacks_queue.py` — update imports
- `tests/test_callbacks_menu.py` — update imports
- `tests/test_callbacks_contacts.py` — update imports
- `tests/test_callbacks_workflows.py` — update imports

**Not modified:**
- `webhook/bot/callback_data.py` — unchanged
- `webhook/bot/states.py` — unchanged
- `webhook/bot/keyboards.py` — unchanged
- `webhook/bot/routers/_helpers.py` — stays shared
- All other production code

---

## Registration order invariant (DO NOT BREAK)

In `main.py`, `callback_router` currently has filter-order dependencies. After split, the same precedence must hold. Register in this order:

```python
dp.include_router(callbacks_curation_router)   # DraftAction.filter(F.action == "adjust") + .filter(F.action == "reject") BEFORE generic DraftAction.filter()
dp.include_router(callbacks_reports_router)    # ReportType, ReportYears, ReportYear, ReportMonth, ReportDownload, ReportBack
dp.include_router(callbacks_queue_router)      # QueuePage, QueueOpen
dp.include_router(callbacks_menu_router)       # MenuAction
dp.include_router(callbacks_contacts_router)   # ContactToggle, ContactPage
dp.include_router(callbacks_workflows_router)  # WorkflowRun, WorkflowList, nop
```

Curation MUST be first (specific `DraftAction.filter(F.action == "adjust")` and `DraftAction.filter(F.action == "reject")` handlers must fire before the generic `DraftAction.filter()` handler). Within curation the decorators in source order already handle this — so keep the source order of curation handlers intact when moving.

---

## Task 1: Pre-flight check

**Files:** none modified.

- [ ] **Step 1: Verify starting state**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && git checkout main && git pull origin main
git log phase1-safety-net-complete --oneline -1
.venv/bin/pytest tests/test_callbacks_*.py tests/test_messages_fsm_isolation.py --tb=no -q 2>&1 | tail -3
```

Expected:
- Current branch is `main`.
- Tag `phase1-safety-net-complete` points to the latest Phase 1 commit (`85cc85b` or newer if follow-ups were added).
- Full new suite: **53 passed** in under 3s.

If any check fails, STOP and report BLOCKED.

- [ ] **Step 2: Create working branch**

```bash
git checkout -b phase2-router-split
```

- [ ] **Step 3: Confirm `callbacks.py` structure**

```bash
wc -l webhook/bot/routers/callbacks.py
grep -c "^@callback_router" webhook/bot/routers/callbacks.py
```

Expected: ~601 lines, ~19 decorator instances.

No commit yet — this is a read-only preflight.

---

## Task 2: Create `callbacks_curation.py` (move 6 handlers)

**Files:**
- Create: `webhook/bot/routers/callbacks_curation.py`
- Modify: `webhook/bot/routers/callbacks.py` (remove the moved handlers)
- Modify: `webhook/bot/main.py` (add new router import + include)
- Modify: `tests/test_callbacks_curation.py` (update handler imports)

### Handlers to move

From `webhook/bot/routers/callbacks.py`:
- `_finalize_card` helper (lines ~51-62)
- `on_draft_adjust` (decorator `@callback_router.callback_query(DraftAction.filter(F.action == "adjust"))`, lines ~67-86)
- `on_draft_reject` (decorator `@callback_router.callback_query(DraftAction.filter(F.action == "reject"))`, lines ~91-130)
- `on_draft_action` (decorator `@callback_router.callback_query(DraftAction.filter())`, lines ~135-176)
- `on_curate_action` (decorator `@callback_router.callback_query(CurateAction.filter())`, lines ~442-563)
- `on_broadcast_confirm` (decorator `@callback_router.callback_query(BroadcastConfirm.filter())`, lines ~568-594)

### Pattern

- [ ] **Step 1: Create `webhook/bot/routers/callbacks_curation.py` with this skeleton**

```python
"""Callback handlers for curation domain: drafts, curation items, broadcast confirm.

Extracted from webhook/bot/routers/callbacks.py during Phase 2 router split.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext

from bot.config import get_bot
from bot.callback_data import CurateAction, DraftAction, BroadcastConfirm
from bot.states import AdjustDraft, RejectReason
from bot.middlewares.auth import RoleMiddleware
from bot.routers._helpers import (
    drafts_get, drafts_contains, drafts_update,
    run_pipeline_and_archive,
)
import redis_queries

logger = logging.getLogger(__name__)

callbacks_curation_router = Router(name="callbacks_curation")
callbacks_curation_router.callback_query.middleware(RoleMiddleware(allowed_roles={"admin"}))


# ── Helper ──

async def _finalize_card(query: CallbackQuery, status_text: str):
    """Edit original message to status_text, removing keyboard. Fallback to new message."""
    bot = get_bot()
    message_id = query.message.message_id
    try:
        await bot.edit_message_text(
            status_text, chat_id=query.message.chat.id,
            message_id=message_id, reply_markup=None,
        )
    except Exception:
        plain = status_text.replace("*", "").replace("`", "").replace("_", "")
        await bot.send_message(query.message.chat.id, plain)


# ── Handlers (paste from callbacks.py, change decorator to @callbacks_curation_router) ──

# PASTE on_draft_adjust here (change decorator prefix)
# PASTE on_draft_reject here
# PASTE on_draft_action here
# PASTE on_curate_action here
# PASTE on_broadcast_confirm here
```

Then, for EACH of the 5 handlers, copy the function body verbatim from `webhook/bot/routers/callbacks.py` and change only the decorator prefix from `@callback_router.callback_query(...)` to `@callbacks_curation_router.callback_query(...)`.

**Note on inline imports:** handlers currently do `from dispatch import process_approval_async` and `from execution.curation import redis_client` inside the function body. KEEP them inline for this task — they will be hoisted in Task 9. Moving them now risks circular imports.

- [ ] **Step 2: Remove the 5 moved handlers from `webhook/bot/routers/callbacks.py`**

Delete the function definitions (from `@callback_router.callback_query(DraftAction.filter(F.action == "adjust"))` through the end of `on_broadcast_confirm`). Also delete `_finalize_card` (lines ~51-62). Remove now-unused imports from the `bot.callback_data` line if `DraftAction`, `CurateAction`, `BroadcastConfirm` are no longer referenced in callbacks.py.

Leave `on_menu_action`, all `on_report_*`, `on_queue_page`, `on_queue_open`, `on_contact_toggle`, `on_contact_page`, `on_workflow_run`, `on_workflow_list`, `on_nop` in place.

- [ ] **Step 3: Update `webhook/bot/main.py`**

Find the line `from bot.routers.callbacks import callback_router` and ADD below it:

```python
from bot.routers.callbacks_curation import callbacks_curation_router
```

Find the line `dp.include_router(callback_router)` and REPLACE with:

```python
dp.include_router(callbacks_curation_router)  # draft/curate/broadcast — specific filters first
dp.include_router(callback_router)             # remaining callbacks (progressively shrinks during Phase 2)
```

- [ ] **Step 4: Update `tests/test_callbacks_curation.py` imports**

Change:
```python
from bot.routers.callbacks import (
    on_draft_adjust, on_draft_reject, on_draft_action,
    on_curate_action, on_broadcast_confirm,
)
```

To:
```python
from bot.routers.callbacks_curation import (
    on_draft_adjust, on_draft_reject, on_draft_action,
    on_curate_action, on_broadcast_confirm,
)
```

Update all `mocker.patch("bot.routers.callbacks.XXX", ...)` calls in this test file to `mocker.patch("bot.routers.callbacks_curation.XXX", ...)` for every symbol imported at module top of `callbacks_curation.py` (`drafts_get`, `drafts_contains`, `drafts_update`, `get_bot`, `redis_queries`). Inline-imported symbols (like `dispatch.process_approval_async`) KEEP their `dispatch.xxx` patch path because the import is inline.

- [ ] **Step 5: Run curation tests**

```bash
.venv/bin/pytest tests/test_callbacks_curation.py -v 2>&1 | tail -25
```

Expected: 14 passed.

If a test fails, the likely cause is a patch path that wasn't updated. Re-grep the test file for `bot.routers.callbacks` and replace with `bot.routers.callbacks_curation` where appropriate (only for module-top imports of callbacks_curation.py).

- [ ] **Step 6: Run full new suite to confirm no regressions elsewhere**

```bash
.venv/bin/pytest tests/test_callbacks_*.py tests/test_messages_fsm_isolation.py --tb=short 2>&1 | tail -10
```

Expected: 53 passed.

- [ ] **Step 7: Start the webhook to smoke-test it boots (no crashes on router import)**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && .venv/bin/python -c "from webhook.bot.main import main; print('imports OK')"
```

Expected: prints "imports OK" with no traceback. A crash here means a circular import or missing symbol.

- [ ] **Step 8: Commit**

```bash
git add webhook/bot/routers/callbacks_curation.py webhook/bot/routers/callbacks.py webhook/bot/main.py tests/test_callbacks_curation.py
git commit -m "refactor(bot): extract curation handlers to callbacks_curation.py"
```

---

## Task 3: Create `callbacks_reports.py` (move 6 handlers)

**Files:**
- Create: `webhook/bot/routers/callbacks_reports.py`
- Modify: `webhook/bot/routers/callbacks.py`
- Modify: `webhook/bot/main.py`
- Modify: `tests/test_callbacks_reports.py`

### Handlers to move

From `callbacks.py`:
- `on_report_type`
- `on_report_years`
- `on_report_year`
- `on_report_month`
- `on_report_download`
- `on_report_back`

- [ ] **Step 1: Create `webhook/bot/routers/callbacks_reports.py`**

```python
"""Callback handlers for report navigation.

Extracted from webhook/bot/routers/callbacks.py during Phase 2 router split.
"""
from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import CallbackQuery

from bot.callback_data import (
    ReportType, ReportYear, ReportMonth, ReportDownload, ReportBack, ReportYears,
)
from bot.middlewares.auth import RoleMiddleware
from reports_nav import (
    reports_show_types, reports_show_latest, reports_show_years,
    reports_show_months, reports_show_month_list, handle_report_download,
)

logger = logging.getLogger(__name__)

callbacks_reports_router = Router(name="callbacks_reports")
callbacks_reports_router.callback_query.middleware(RoleMiddleware(allowed_roles={"admin"}))


# PASTE on_report_type through on_report_back — change @callback_router to @callbacks_reports_router.
```

Copy each function body verbatim from `callbacks.py`, only changing the decorator prefix.

- [ ] **Step 2: Remove the 6 handlers from `callbacks.py`**

Remove the report handlers. If the `reports_nav` import is no longer used in `callbacks.py`, remove it.

- [ ] **Step 3: Update `main.py`**

Add after curation import:
```python
from bot.routers.callbacks_reports import callbacks_reports_router
```

Add after curation include:
```python
dp.include_router(callbacks_reports_router)
```

- [ ] **Step 4: Update `tests/test_callbacks_reports.py` imports**

Change `from bot.routers.callbacks import (...)` to `from bot.routers.callbacks_reports import (...)`. Change `mocker.patch("bot.routers.callbacks.reports_show_*", ...)` calls to `mocker.patch("bot.routers.callbacks_reports.reports_show_*", ...)` for every `reports_show_*` and `handle_report_download` patch.

- [ ] **Step 5: Run tests**

```bash
.venv/bin/pytest tests/test_callbacks_reports.py -v 2>&1 | tail -15
.venv/bin/pytest tests/test_callbacks_*.py tests/test_messages_fsm_isolation.py --tb=no -q 2>&1 | tail -3
```

Expected: 8 passed on reports. 53 passed overall.

- [ ] **Step 6: Smoke test import**

```bash
.venv/bin/python -c "from webhook.bot.main import main; print('imports OK')"
```

- [ ] **Step 7: Commit**

```bash
git add webhook/bot/routers/callbacks_reports.py webhook/bot/routers/callbacks.py webhook/bot/main.py tests/test_callbacks_reports.py
git commit -m "refactor(bot): extract report navigation handlers to callbacks_reports.py"
```

---

## Task 4: Create `callbacks_queue.py` (move 2 handlers)

**Files:**
- Create: `webhook/bot/routers/callbacks_queue.py`
- Modify: `callbacks.py`, `main.py`, `tests/test_callbacks_queue.py`

### Handlers

- `on_queue_page` (`QueuePage.filter()`)
- `on_queue_open` (`QueueOpen.filter()`)

- [ ] **Step 1: Create `webhook/bot/routers/callbacks_queue.py`**

```python
"""Callback handlers for queue navigation.

Extracted from webhook/bot/routers/callbacks.py during Phase 2 router split.
"""
from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Router
from aiogram.types import CallbackQuery

from bot.callback_data import QueuePage, QueueOpen
from bot.config import get_bot
from bot.middlewares.auth import RoleMiddleware
import query_handlers

logger = logging.getLogger(__name__)

callbacks_queue_router = Router(name="callbacks_queue")
callbacks_queue_router.callback_query.middleware(RoleMiddleware(allowed_roles={"admin"}))


# PASTE on_queue_page and on_queue_open here — change decorator prefix.
```

- [ ] **Step 2: Remove from `callbacks.py`**. Remove `QueuePage`, `QueueOpen` imports from `callbacks.py` top if no longer used.

- [ ] **Step 3: Update `main.py`** (add import + include after reports).

- [ ] **Step 4: Update `tests/test_callbacks_queue.py`** — change `from bot.routers.callbacks import ...` to `from bot.routers.callbacks_queue import ...`. Update `mocker.patch("bot.routers.callbacks.query_handlers.*", ...)` to `mocker.patch("bot.routers.callbacks_queue.query_handlers.*", ...)`. Update `mocker.patch("bot.routers.callbacks.get_bot", ...)` to `mocker.patch("bot.routers.callbacks_queue.get_bot", ...)`.

- [ ] **Step 5: Run `pytest tests/test_callbacks_queue.py -v` → 5 passed. Then full suite: 53 passed.**

- [ ] **Step 6: Smoke test import.**

- [ ] **Step 7: Commit:** `refactor(bot): extract queue handlers to callbacks_queue.py`

---

## Task 5: Create `callbacks_menu.py` (move 1 handler)

**Files:**
- Create: `webhook/bot/routers/callbacks_menu.py`
- Modify: `callbacks.py`, `main.py`, `tests/test_callbacks_menu.py`

### Handlers

- `on_menu_action` (`MenuAction.filter()`) — 66 lines, 13 branches. Move the whole function intact.

- [ ] **Step 1: Create `webhook/bot/routers/callbacks_menu.py`**

```python
"""Main-menu switchboard handler.

Extracted from webhook/bot/routers/callbacks.py during Phase 2 router split.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram import Router
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext

from bot.callback_data import MenuAction
from bot.middlewares.auth import RoleMiddleware
from bot.states import WriterInput, BroadcastMessage
from reports_nav import reports_show_types
from status_builder import build_status_message
import query_handlers

logger = logging.getLogger(__name__)

callbacks_menu_router = Router(name="callbacks_menu")
callbacks_menu_router.callback_query.middleware(RoleMiddleware(allowed_roles={"admin"}))


# PASTE on_menu_action here — change @callback_router to @callbacks_menu_router.
```

**Note:** The handler uses `WriterInput` and `BroadcastMessage` from `bot.states` inline (`from bot.states import WriterInput` / `from bot.states import BroadcastMessage`). Since we're importing both at module top now, REMOVE those inline imports from the function body.

- [ ] **Step 2: Remove `on_menu_action` from `callbacks.py`**. Remove `MenuAction`, `WriterInput`, `BroadcastMessage`, `build_status_message`, `query_handlers`, `reports_show_types` imports from `callbacks.py` top if no longer referenced.

- [ ] **Step 3: Update `main.py`** (add import + include after queue).

- [ ] **Step 4: Update `tests/test_callbacks_menu.py`** imports. Change `from bot.routers.callbacks import on_menu_action` to `from bot.routers.callbacks_menu import on_menu_action`. Update patches: `mocker.patch("bot.routers.callbacks.reports_show_types", ...)` → `mocker.patch("bot.routers.callbacks_menu.reports_show_types", ...)`, and similarly for `query_handlers.*`.

- [ ] **Step 5: Run `pytest tests/test_callbacks_menu.py -v` → 8 passed. Then full suite: 53 passed.**

- [ ] **Step 6: Smoke test import.**

- [ ] **Step 7: Commit:** `refactor(bot): extract main menu switchboard to callbacks_menu.py`

---

## Task 6: Create `callbacks_contacts.py` (move 2 handlers)

**Files:**
- Create: `webhook/bot/routers/callbacks_contacts.py`
- Modify: `callbacks.py`, `main.py`, `tests/test_callbacks_contacts.py`

### Handlers

- `on_contact_toggle` (`ContactToggle.filter()`)
- `on_contact_page` (`ContactPage.filter()`)

- [ ] **Step 1: Create `webhook/bot/routers/callbacks_contacts.py`**

```python
"""Callback handlers for contact admin (toggle/list).

Extracted from webhook/bot/routers/callbacks.py during Phase 2 router split.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Router
from aiogram.types import CallbackQuery

from bot.callback_data import ContactToggle, ContactPage
from bot.config import SHEET_ID
from bot.middlewares.auth import RoleMiddleware
from execution.integrations.sheets_client import SheetsClient

logger = logging.getLogger(__name__)

callbacks_contacts_router = Router(name="callbacks_contacts")
callbacks_contacts_router.callback_query.middleware(RoleMiddleware(allowed_roles={"admin"}))


# PASTE on_contact_toggle and on_contact_page here — change decorator prefix.
```

The handlers use `from bot.routers.commands import _render_list_view` inline. KEEP that inline (avoids circular import risk with commands.py).

- [ ] **Step 2: Remove from `callbacks.py`.**

- [ ] **Step 3: Update `main.py`** (add import + include after menu).

- [ ] **Step 4: Update `tests/test_callbacks_contacts.py`** imports. Change module path `bot.routers.callbacks` → `bot.routers.callbacks_contacts` for function imports and the `SheetsClient` patch path. The `bot.routers.commands._render_list_view` patch stays (it's imported from the commands module at runtime inline).

- [ ] **Step 5: Run `pytest tests/test_callbacks_contacts.py -v` → 4 passed. Then full: 53 passed.**

- [ ] **Step 6: Smoke test import.**

- [ ] **Step 7: Commit:** `refactor(bot): extract contact admin handlers to callbacks_contacts.py`

---

## Task 7: Create `callbacks_workflows.py` (move 3 handlers, DELETE callbacks.py)

**Files:**
- Create: `webhook/bot/routers/callbacks_workflows.py`
- Delete: `webhook/bot/routers/callbacks.py`
- Modify: `webhook/bot/main.py`, `tests/test_callbacks_workflows.py`

### Handlers

- `on_workflow_run` (`WorkflowRun.filter()`)
- `on_workflow_list` (`WorkflowList.filter()`)
- `on_nop` (`lambda q: q.data in ("nop", "noop")`)

- [ ] **Step 1: Create `webhook/bot/routers/callbacks_workflows.py`**

```python
"""Callback handlers for workflow triggers + nop.

Extracted from webhook/bot/routers/callbacks.py during Phase 2 router split.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Router
from aiogram.types import CallbackQuery

from bot.callback_data import WorkflowRun, WorkflowList
from bot.config import get_bot
from bot.keyboards import build_main_menu_keyboard
from bot.middlewares.auth import RoleMiddleware

logger = logging.getLogger(__name__)

callbacks_workflows_router = Router(name="callbacks_workflows")
callbacks_workflows_router.callback_query.middleware(RoleMiddleware(allowed_roles={"admin"}))


# PASTE on_workflow_run, on_workflow_list, on_nop here — change @callback_router to @callbacks_workflows_router.
```

- [ ] **Step 2: Delete `webhook/bot/routers/callbacks.py`**

At this point all handlers have been moved. Confirm `callbacks.py` is empty of decorated handlers:
```bash
grep -c "^@callback_router" webhook/bot/routers/callbacks.py
```
Expected: 0.

Delete the file:
```bash
git rm webhook/bot/routers/callbacks.py
```

- [ ] **Step 3: Update `main.py`**

Remove the line `from bot.routers.callbacks import callback_router` and the line `dp.include_router(callback_router)`.

Add:
```python
from bot.routers.callbacks_workflows import callbacks_workflows_router
```

Add to the include chain (at end of curation→reports→queue→menu→contacts):
```python
dp.include_router(callbacks_workflows_router)
```

Final order in `main.py`:
```python
dp.include_router(onboarding_router)
dp.include_router(public_router)
dp.include_router(admin_router)
dp.include_router(shared_router)
dp.include_router(callbacks_curation_router)
dp.include_router(callbacks_reports_router)
dp.include_router(callbacks_queue_router)
dp.include_router(callbacks_menu_router)
dp.include_router(callbacks_contacts_router)
dp.include_router(callbacks_workflows_router)
dp.include_router(reply_kb_router)
dp.include_router(message_router)
```

- [ ] **Step 4: Update `tests/test_callbacks_workflows.py`** imports from `bot.routers.callbacks` → `bot.routers.callbacks_workflows` for `on_workflow_run`, `on_workflow_list`, `on_nop`. Update `mocker.patch("bot.routers.callbacks.get_bot", ...)` → `mocker.patch("bot.routers.callbacks_workflows.get_bot", ...)`. Update `mocker.patch("bot.routers.callbacks.build_main_menu_keyboard", ...)` → `mocker.patch("bot.routers.callbacks_workflows.build_main_menu_keyboard", ...)`.

- [ ] **Step 5: Run full suite**

```bash
.venv/bin/pytest tests/test_callbacks_*.py tests/test_messages_fsm_isolation.py -v 2>&1 | tail -20
```

Expected: 53 passed. Zero references to `bot.routers.callbacks` (the deleted module) in any test file. Verify:
```bash
grep -r "bot.routers.callbacks\b" tests/ | grep -v "bot.routers.callbacks_"
```
Expected: no results.

- [ ] **Step 6: Smoke test import**

```bash
.venv/bin/python -c "from webhook.bot.main import main; print('imports OK')"
```

- [ ] **Step 7: Verify file sizes (all new routers should be <200 lines)**

```bash
wc -l webhook/bot/routers/callbacks_*.py
```

Expected output approximately:
- callbacks_curation.py: ~180
- callbacks_reports.py: ~140
- callbacks_queue.py: ~80
- callbacks_menu.py: ~100
- callbacks_contacts.py: ~60
- callbacks_workflows.py: ~100

If any file exceeds 200 lines, it is OK but note in the commit message.

- [ ] **Step 8: Commit**

```bash
git add webhook/bot/routers/callbacks_workflows.py webhook/bot/main.py tests/test_callbacks_workflows.py
git rm webhook/bot/routers/callbacks.py
git commit -m "refactor(bot): extract workflow handlers + delete callbacks.py god file"
```

---

## Task 8: Reply-keyboard handler consolidation

**Files:**
- Modify: `webhook/bot/routers/messages.py`

### Pre-check

Before modifying, determine the current decoration state:

```bash
grep -E "^@(message_router|reply_kb_router)" webhook/bot/routers/messages.py
```

Look at each `on_reply_*` function (7 of them: `on_reply_reports`, `on_reply_queue`, `on_reply_workflows`, `on_reply_settings`, `on_reply_writer`, `on_reply_broadcast`, `on_reply_admin`). Each is decorated with either `@message_router` or `@reply_kb_router`.

**If all 7 are already decorated with `@reply_kb_router`:** the consolidation is already done — skip this task and just commit an empty marker: `git commit --allow-empty -m "refactor(bot): reply_kb consolidation already in place — no-op"`.

**If any are decorated with `@message_router`:** proceed with steps below.

- [ ] **Step 1: For each `on_reply_*` handler currently decorated with `@message_router.message(F.text == "...")`, change the decorator to `@reply_kb_router.message(F.text == "...")`**

Do NOT change function bodies. Do NOT change the text matchers. Only the decorator prefix.

- [ ] **Step 2: Run the FSM isolation test file**

```bash
.venv/bin/pytest tests/test_messages_fsm_isolation.py -v 2>&1 | tail -15
```

Expected: 8 passed. The regression-guard test (`test_message_router_has_no_catchall_text_handler_without_state_filter`) is the key check — it will fail if any non-state-filtered handler remains on `message_router`.

- [ ] **Step 3: Run full new suite**

```bash
.venv/bin/pytest tests/test_callbacks_*.py tests/test_messages_fsm_isolation.py --tb=no -q 2>&1 | tail -3
```

Expected: 53 passed.

- [ ] **Step 4: Smoke test bot import**

```bash
.venv/bin/python -c "from webhook.bot.main import main; print('imports OK')"
```

- [ ] **Step 5: Commit**

```bash
git add webhook/bot/routers/messages.py
git commit -m "refactor(bot): move on_reply_* handlers to reply_kb_router"
```

---

## Task 9: Inline imports cleanup

**Files:**
- Modify: `webhook/bot/routers/callbacks_curation.py`, `callbacks_menu.py`, `callbacks_workflows.py`, `callbacks_contacts.py` (any file with inline imports in function bodies)

### Background

Several handlers had inline `from X import Y` inside function bodies in the original `callbacks.py`. During Tasks 2-7 these were preserved to avoid circular-import surprises. Now that the domain boundaries are clear, hoist them to module top where safe.

### Strategy

For each new router file, grep for inline imports:

```bash
grep -n "^\s\+from\|^\s\+import" webhook/bot/routers/callbacks_*.py | grep -v "^[^:]*:\s*[0-9]\+:from __future__" | head -30
```

For each inline import found, attempt to hoist it to module top by:

1. Moving the `from X import Y` to the import section at top.
2. Removing the inline occurrence.
3. Running `.venv/bin/python -c "from webhook.bot.main import main"` to verify no circular import.

**If a circular import surfaces (ImportError), REVERT that one import and leave it inline with a one-line comment:**
```python
# cyclic: commands.py imports from callbacks_contacts
from bot.routers.commands import _render_list_view
```

### Known candidates (from Phase 2 analysis)

- `callbacks_curation.py`: `from dispatch import process_approval_async`, `from dispatch import process_test_send_async`, `from execution.curation import redis_client`
- `callbacks_menu.py`: `from reports_nav import reports_show_types` (already at top), `from status_builder import build_status_message` (already at top?)
- `callbacks_workflows.py`: `from workflow_trigger import trigger_workflow, find_triggered_run, poll_and_update, _workflow_name_by_id, render_workflow_list`
- `callbacks_contacts.py`: `from bot.routers.commands import _render_list_view` (likely cyclic — leave inline)

- [ ] **Step 1: For each candidate, attempt hoist one at a time and run the import smoke test after each change**

```bash
.venv/bin/python -c "from webhook.bot.main import main; print('imports OK')"
```

If success: keep the hoist. If failure: revert and add a cycle comment.

- [ ] **Step 2: Run the full new suite**

```bash
.venv/bin/pytest tests/test_callbacks_*.py tests/test_messages_fsm_isolation.py --tb=no -q 2>&1 | tail -3
```

Expected: 53 passed. Note: some tests patched inline-import paths (like `dispatch.process_approval_async`). After hoisting, those patch paths become `bot.routers.callbacks_curation.process_approval_async`. UPDATE THE TEST FILE PATCHES only where you actually hoisted an import. For imports left inline, keep the test patch path as-is.

- [ ] **Step 3: Commit**

```bash
git add webhook/bot/routers/ tests/
git commit -m "refactor(bot): hoist inline imports where no circular dependency"
```

---

## Task 10: Final verification + tag

**Files:** none modified.

- [ ] **Step 1: Full repo test suite**

```bash
.venv/bin/pytest --tb=short 2>&1 | tail -10
```

Expected: 414+ passed, 5 pre-existing failures unchanged. The new 53 Phase 1 tests must all still pass.

- [ ] **Step 2: File-size verification**

```bash
wc -l webhook/bot/routers/callbacks_*.py
```

All files ≤ 200 lines.

- [ ] **Step 3: No references to old module**

```bash
grep -r "bot.routers.callbacks\b" --include="*.py" . | grep -v "bot.routers.callbacks_"
```

Expected: zero results (outside of comments/docs).

- [ ] **Step 4: Manual bot smoke test**

The automated suite covers the router internals. Manually trigger these to confirm end-to-end still works:

1. Open Telegram bot.
2. Tap `/menu` (or the reply keyboard "🥸 Admin" button) → main menu inline keyboard should appear.
3. Tap "📊 Reports" → report types list appears.
4. Tap one type → latest reports or years list appears.
5. Tap a report download → the PDF is sent to chat.
6. Tap "📰 Fila" → queue page renders with pagination if >1 page.
7. If there's a pending draft: open it, test `Ajustar` (enters FSM), `Rejeitar` (enters FSM), `Aprovar` (dispatches).
8. Tap "⚡ Workflows" → list renders. Tap one → triggers. Poll message updates.
9. Tap "📲 Enviar Msg" → enters BroadcastMessage.waiting_text FSM. Send a text → preview + Confirm button → send to a test group.

Each flow must behave identically to before Phase 2. Any regression means a patch path was missed or a filter was dropped.

- [ ] **Step 5: Tag and merge**

```bash
git commit --allow-empty -m "refactor(bot): phase 2 router split complete"
git tag phase2-router-split-complete
git log --oneline phase1-safety-net-complete..HEAD
```

The log should show ~8-10 refactor commits.

- [ ] **Step 6: Merge to main and push**

```bash
git checkout main
git merge phase2-router-split --ff-only
git push origin main
git push origin phase2-router-split-complete
```

---

## Self-Review Notes

1. **Spec coverage (§3):** every sub-component is a task — 3.1 split (Tasks 2-7), 3.2 reply handlers (Task 8), 3.3 inline imports (Task 9), smoke test (Task 10).
2. **Placeholder scan:** no "TBD" or "similar to above". Each task has concrete file names, decorator changes, patch paths.
3. **Consistency:**
   - Router naming: `callbacks_<domain>_router` across all 6 files.
   - Test patch paths: `bot.routers.callbacks_<domain>.<symbol>` for module-top imports; inline imports keep their source-module path.
   - Registration order in `main.py` consistently curation → reports → queue → menu → contacts → workflows.
4. **Known risks:**
   - Task 9 inline imports may hit circular-import cycles; the revert-and-comment fallback is documented.
   - Task 8 may be a no-op if reply handlers are already on `reply_kb_router`; an empty commit marker is specified.

---

*Plan author: writing-plans session 2026-04-18*
*Next phase plan: `docs/superpowers/plans/2026-04-18-phase3-observability.md` (written after Phase 2 ships).*
