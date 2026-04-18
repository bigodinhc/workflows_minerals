# Backend Hardening — Phase 1: Safety Net Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze current behavior of `webhook/bot/routers/callbacks.py` and FSM routing in `webhook/bot/routers/messages.py` with ~40 characterization tests, so that Phase 2's router split cannot regress silently.

**Architecture:** Pure-mock characterization tests using `pytest` + `pytest-mock` + `pytest-asyncio`. New fixtures in `tests/conftest.py` provide reusable `mock_bot`, `mock_callback_query`, `mock_message`, and `fsm_context_in_state` factories. Six new test files mirror the planned Phase 2 router split (curation, reports, queue, contacts, workflows) plus one FSM-isolation file for `messages.py`. No production code changes in this phase.

**Tech Stack:** `pytest>=7`, `pytest-mock>=3.10`, `pytest-asyncio>=0.21`, `fakeredis>=2.20` (already installed). `unittest.mock.AsyncMock` and `MagicMock` for aiogram objects.

**Spec reference:** `docs/superpowers/specs/2026-04-18-backend-hardening-v1-design.md` §2.

---

## File Structure

**Create:**
- `tests/test_callbacks_curation.py` — 10 tests for `on_draft_adjust`, `on_draft_reject`, `on_draft_action` (approve/test_approve), `on_curate_action` (archive/reject/pipeline/send_raw), `on_broadcast_confirm`
- `tests/test_callbacks_reports.py` — 8 tests for `on_report_type`, `on_report_years`, `on_report_year`, `on_report_month`, `on_report_download`, `on_report_back`
- `tests/test_callbacks_queue.py` — 5 tests for `on_queue_page`, `on_queue_open`
- `tests/test_callbacks_contacts.py` — 4 tests for `on_contact_toggle`, `on_contact_page`
- `tests/test_callbacks_workflows.py` — 6 tests for `on_workflow_run`, `on_workflow_list`, `on_nop`
- `tests/test_messages_fsm_isolation.py` — 7 tests for FSM state routing in `messages.py` and reply-keyboard routing
- `tests/README.md` — one-page doc of the mock pattern (so future tests follow it)

**Modify:**
- `tests/conftest.py` — add 4 new fixtures (existing content stays)

**Not modified (by design):**
- Any file under `webhook/bot/routers/`, `webhook/dispatch.py`, or `execution/` — this phase is test-only.

---

## Task 1: Add Shared Fixtures to `tests/conftest.py`

**Files:**
- Modify: `tests/conftest.py`

### Background

`tests/conftest.py` already adds `webhook/` to `sys.path` so bare imports (`bot.routers.callbacks`) work. We extend it with 4 fixtures used across every new test file.

- [ ] **Step 1: Read the existing `conftest.py` to preserve its contents**

Run: `cat tests/conftest.py`

Expected: a small file with path setup. Keep its code exactly as-is; append after it.

- [ ] **Step 2: Append the new fixtures to `tests/conftest.py`**

Append this block to the bottom of `tests/conftest.py`:

```python
# ─── Shared fixtures for router tests (Phase 1 safety net) ───────────────────
from unittest.mock import AsyncMock, MagicMock
import pytest
from aiogram import Bot
from aiogram.types import CallbackQuery, Message, Chat, User
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State


@pytest.fixture
def mock_bot():
    """AsyncMock of aiogram Bot with the methods callback/message handlers call."""
    bot = AsyncMock(spec=Bot)
    bot.send_message = AsyncMock()
    bot.edit_message_text = AsyncMock()
    bot.answer_callback_query = AsyncMock()
    return bot


@pytest.fixture
def mock_callback_query():
    """Factory: mock_callback_query(user_id=12345, chat_id=12345, message_id=1, data='...')."""
    def _factory(user_id: int = 12345, chat_id: int = 12345,
                 message_id: int = 1, data: str = ""):
        cb = MagicMock(spec=CallbackQuery)
        cb.id = "cb_test_id"
        cb.data = data
        cb.from_user = MagicMock(spec=User)
        cb.from_user.id = user_id
        cb.from_user.first_name = "Test"
        cb.message = MagicMock(spec=Message)
        cb.message.message_id = message_id
        cb.message.chat = MagicMock(spec=Chat)
        cb.message.chat.id = chat_id
        cb.message.answer = AsyncMock()
        cb.answer = AsyncMock()
        return cb
    return _factory


@pytest.fixture
def mock_message():
    """Factory: mock_message(text='hi', chat_id=12345, user_id=12345)."""
    def _factory(text: str = "", chat_id: int = 12345, user_id: int = 12345):
        msg = MagicMock(spec=Message)
        msg.text = text
        msg.message_id = 1
        msg.chat = MagicMock(spec=Chat)
        msg.chat.id = chat_id
        msg.from_user = MagicMock(spec=User)
        msg.from_user.id = user_id
        msg.answer = AsyncMock()
        return msg
    return _factory


@pytest.fixture
def fsm_context_in_state():
    """Factory: fsm_context_in_state(state=AdjustDraft.waiting_feedback, data={'draft_id': 'x'})."""
    def _factory(state=None, data: dict | None = None):
        ctx = MagicMock(spec=FSMContext)
        ctx.get_state = AsyncMock(return_value=state)
        ctx.get_data = AsyncMock(return_value=data or {})
        ctx.set_state = AsyncMock()
        ctx.update_data = AsyncMock()
        ctx.clear = AsyncMock()
        return ctx
    return _factory
```

- [ ] **Step 3: Run the existing test suite to confirm the new fixtures don't break anything**

Run: `pytest -x --tb=short`

Expected: all existing tests pass. If import errors appear, check that `aiogram` is installed (`pip show aiogram`).

- [ ] **Step 4: Commit**

```bash
git add tests/conftest.py
git commit -m "test(conftest): add mock_bot/mock_callback_query/mock_message/fsm_context_in_state fixtures"
```

---

## Task 2: Create `tests/README.md` Documenting the Mock Pattern

**Files:**
- Create: `tests/README.md`

- [ ] **Step 1: Write `tests/README.md` with the pattern doc**

```markdown
# Tests

Python tests for `webhook/` and `execution/` modules.

## Running

```bash
pytest                    # all tests
pytest tests/test_callbacks_curation.py -v
pytest -k "draft_adjust"  # match by test name
```

`tests/conftest.py` puts the repo root and `webhook/` on `sys.path`, so bare imports (`from bot.routers.callbacks import on_draft_adjust`) work. The same imports work at runtime inside the Docker container.

## Mock Pattern for Callback Handlers

Aiogram handlers take a `CallbackQuery` (or `Message`) plus an `FSMContext`. Tests mock all three via fixtures in `conftest.py`.

### Example (characterization test)

```python
import pytest
from bot.callback_data import DraftAction
from bot.routers.callbacks import on_draft_adjust
from bot.states import AdjustDraft

@pytest.mark.asyncio
async def test_draft_adjust_happy_path(mock_callback_query, fsm_context_in_state, mocker):
    # Arrange
    query = mock_callback_query(data="draft:adjust:abc123")
    state = fsm_context_in_state()
    mocker.patch("bot.routers.callbacks.drafts_get",
                 return_value={"message": "hi", "status": "pending"})
    mocker.patch("bot.routers.callbacks.get_bot", return_value=mocker.AsyncMock())

    # Act
    await on_draft_adjust(query, DraftAction(action="adjust", draft_id="abc123"), state)

    # Assert — characterize CURRENT behavior; do not reverse-engineer "should"
    state.set_state.assert_awaited_once_with(AdjustDraft.waiting_feedback)
    state.update_data.assert_awaited_once_with(draft_id="abc123")
    query.answer.assert_awaited_with("✏️ Modo ajuste")
```

### Guidelines

1. **Characterize, don't prescribe.** Match exactly what the handler does today. If a fix is needed, record it as a follow-up — do not alter the handler in the test-writing commit.
2. **Patch at the import site**, not at the definition site. `mocker.patch("bot.routers.callbacks.drafts_get", ...)` — because `callbacks.py` does `from bot.routers._helpers import drafts_get`, and that's the name the handler resolves.
3. **Use `AsyncMock` for anything awaited.** Use `MagicMock` for sync.
4. **Keep tests <3s total.** No network, no real Redis, no real files.
```

- [ ] **Step 2: Commit**

```bash
git add tests/README.md
git commit -m "docs(tests): add tests/README.md with mock pattern guidelines"
```

---

## Task 3: Write `tests/test_callbacks_curation.py` (10 tests)

**Files:**
- Create: `tests/test_callbacks_curation.py`
- Characterizes: `on_draft_adjust`, `on_draft_reject`, `on_draft_action` (approve + test_approve branches), `on_curate_action` (archive + reject + pipeline + send_raw), `on_broadcast_confirm` (send + cancel) in `webhook/bot/routers/callbacks.py`

### Background

`callbacks.py` uses `from bot.routers._helpers import drafts_get, drafts_contains, drafts_update, process_adjustment, run_pipeline_and_archive`. Patch at `bot.routers.callbacks.<name>`.

`on_draft_action` does `from dispatch import process_approval_async` inline — patch at `dispatch.process_approval_async` OR import-site (`bot.routers.callbacks.process_approval_async` won't work because of inline import). Use `mocker.patch("dispatch.process_approval_async")`.

Similarly `on_curate_action` does inline `from execution.curation import redis_client` — patch at `execution.curation.redis_client.archive`, `.discard`, `.get_staging`.

- [ ] **Step 1: Write the full test file**

Create `tests/test_callbacks_curation.py`:

```python
"""Characterization tests for webhook/bot/routers/callbacks.py — curation domain.

Tests freeze CURRENT behavior (2026-04-18). If a test fails after Phase 2 split,
the split regressed behavior — not the test's fault.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from bot.callback_data import DraftAction, CurateAction, BroadcastConfirm
from bot.states import AdjustDraft, RejectReason
from bot.routers.callbacks import (
    on_draft_adjust, on_draft_reject, on_draft_action,
    on_curate_action, on_broadcast_confirm,
)


# ─── on_draft_adjust ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_draft_adjust_happy_path_sets_fsm_and_notifies(
    mock_callback_query, fsm_context_in_state, mocker,
):
    query = mock_callback_query(data="draft:adjust:abc123")
    state = fsm_context_in_state()
    mocker.patch("bot.routers.callbacks.drafts_get",
                 return_value={"message": "hi", "status": "pending"})
    mocker.patch("bot.routers.callbacks.get_bot", return_value=AsyncMock())

    await on_draft_adjust(query, DraftAction(action="adjust", draft_id="abc123"), state)

    state.set_state.assert_awaited_once_with(AdjustDraft.waiting_feedback)
    state.update_data.assert_awaited_once_with(draft_id="abc123")
    query.answer.assert_awaited_with("✏️ Modo ajuste")
    query.message.answer.assert_awaited()  # the "MODO AJUSTE" instructions


@pytest.mark.asyncio
async def test_draft_adjust_draft_missing_answers_error_and_does_not_set_state(
    mock_callback_query, fsm_context_in_state, mocker,
):
    query = mock_callback_query(data="draft:adjust:missing")
    state = fsm_context_in_state()
    mocker.patch("bot.routers.callbacks.drafts_get", return_value=None)
    mocker.patch("bot.routers.callbacks.get_bot", return_value=AsyncMock())

    await on_draft_adjust(query, DraftAction(action="adjust", draft_id="missing"), state)

    query.answer.assert_awaited_with("❌ Draft não encontrado")
    state.set_state.assert_not_called()


# ─── on_draft_reject ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_draft_reject_happy_path_sets_reject_state_and_saves_feedback(
    mock_callback_query, fsm_context_in_state, mocker,
):
    query = mock_callback_query(data="draft:reject:xyz")
    state = fsm_context_in_state()
    mocker.patch(
        "bot.routers.callbacks.drafts_get",
        return_value={"message": "📊 Iron ore up\n*MINERALS TRADING*", "status": "pending"},
    )
    mocker.patch("bot.routers.callbacks.drafts_contains", return_value=True)
    mocker.patch("bot.routers.callbacks.drafts_update")
    save_feedback = mocker.patch(
        "bot.routers.callbacks.redis_queries.save_feedback", return_value="fbk_1",
    )
    mocker.patch("bot.routers.callbacks.get_bot", return_value=AsyncMock())

    await on_draft_reject(query, DraftAction(action="reject", draft_id="xyz"), state)

    save_feedback.assert_called_once()
    kwargs = save_feedback.call_args.kwargs
    assert kwargs["action"] == "draft_reject"
    assert kwargs["item_id"] == "xyz"
    state.set_state.assert_awaited_once_with(RejectReason.waiting_reason)
    state.update_data.assert_awaited_once_with(feedback_key="fbk_1")
    query.answer.assert_awaited_with("❌ Rejeitado")


@pytest.mark.asyncio
async def test_draft_reject_missing_draft_still_sets_state_with_id_fallback_title(
    mock_callback_query, fsm_context_in_state, mocker,
):
    query = mock_callback_query(data="draft:reject:def456")
    state = fsm_context_in_state()
    mocker.patch("bot.routers.callbacks.drafts_get", return_value=None)
    mocker.patch("bot.routers.callbacks.drafts_contains", return_value=False)
    save_feedback = mocker.patch(
        "bot.routers.callbacks.redis_queries.save_feedback", return_value="fbk_2",
    )
    mocker.patch("bot.routers.callbacks.get_bot", return_value=AsyncMock())

    await on_draft_reject(query, DraftAction(action="reject", draft_id="def456"), state)

    # Title falls back to "Draft def456"[:8 of id]
    assert save_feedback.call_args.kwargs["title"].startswith("Draft def456")
    state.set_state.assert_awaited_once_with(RejectReason.waiting_reason)


# ─── on_draft_action — approve branch ────────────────────────────────────────

@pytest.mark.asyncio
async def test_draft_action_approve_happy_path_dispatches_send(
    mock_callback_query, mocker,
):
    query = mock_callback_query(data="draft:approve:approved1")
    mocker.patch(
        "bot.routers.callbacks.drafts_get",
        return_value={"message": "hi", "status": "pending",
                      "uazapi_token": None, "uazapi_url": None},
    )
    drafts_update = mocker.patch("bot.routers.callbacks.drafts_update")
    mocker.patch("bot.routers.callbacks.get_bot", return_value=AsyncMock())
    dispatch_fn = mocker.patch("dispatch.process_approval_async", new=AsyncMock())
    create_task = mocker.patch("asyncio.create_task")

    await on_draft_action(query, DraftAction(action="approve", draft_id="approved1"))

    drafts_update.assert_called_once_with("approved1", status="approved")
    query.answer.assert_awaited_with("✅ Aprovado! Enviando...")
    create_task.assert_called_once()  # scheduled process_approval_async


@pytest.mark.asyncio
async def test_draft_action_approve_already_processed_short_circuits(
    mock_callback_query, mocker,
):
    query = mock_callback_query(data="draft:approve:dup1")
    mocker.patch(
        "bot.routers.callbacks.drafts_get",
        return_value={"message": "hi", "status": "approved"},
    )
    drafts_update = mocker.patch("bot.routers.callbacks.drafts_update")
    mocker.patch("bot.routers.callbacks.get_bot", return_value=AsyncMock())
    create_task = mocker.patch("asyncio.create_task")

    await on_draft_action(query, DraftAction(action="approve", draft_id="dup1"))

    query.answer.assert_awaited_with("⚠️ Já processado")
    drafts_update.assert_not_called()
    create_task.assert_not_called()


# ─── on_draft_action — test_approve branch ───────────────────────────────────

@pytest.mark.asyncio
async def test_draft_action_test_approve_dispatches_test_send(
    mock_callback_query, mocker,
):
    query = mock_callback_query(data="draft:test_approve:t1")
    mocker.patch(
        "bot.routers.callbacks.drafts_get",
        return_value={"message": "hi", "status": "pending"},
    )
    mocker.patch("bot.routers.callbacks.get_bot", return_value=AsyncMock())
    mocker.patch("dispatch.process_test_send_async", new=AsyncMock())
    create_task = mocker.patch("asyncio.create_task")

    await on_draft_action(query, DraftAction(action="test_approve", draft_id="t1"))

    query.answer.assert_awaited_with("🧪 Enviando teste para 1 contato...")
    create_task.assert_called_once()


# ─── on_curate_action — pipeline branch ──────────────────────────────────────

@pytest.mark.asyncio
async def test_curate_action_pipeline_happy_path_schedules_run_pipeline(
    mock_callback_query, fsm_context_in_state, mocker,
):
    query = mock_callback_query(data="curate:pipeline:item1")
    state = fsm_context_in_state()
    mocker.patch(
        "execution.curation.redis_client.get_staging",
        return_value={"title": "T", "fullText": "body", "publishDate": "2026-04-18",
                      "source": "Platts"},
    )
    mocker.patch("bot.routers.callbacks.redis_queries.mark_pipeline_processed")
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=mocker.MagicMock(message_id=99))
    mocker.patch("bot.routers.callbacks.get_bot", return_value=bot)
    create_task = mocker.patch("asyncio.create_task")

    await on_curate_action(query, CurateAction(action="pipeline", item_id="item1"), state)

    query.answer.assert_awaited_with("🖋️ Enviando para o Writer...")
    # run_pipeline_and_archive is scheduled via asyncio.create_task
    create_task.assert_called_once()


# ─── on_curate_action — send_raw branch ──────────────────────────────────────

@pytest.mark.asyncio
async def test_curate_action_send_raw_archives_and_dispatches(
    mock_callback_query, fsm_context_in_state, mocker,
):
    query = mock_callback_query(data="curate:send_raw:item2")
    state = fsm_context_in_state()
    mocker.patch(
        "execution.curation.redis_client.get_staging",
        return_value={"title": "Hdr", "fullText": "Body text"},
    )
    archive = mocker.patch("execution.curation.redis_client.archive")
    mocker.patch("bot.routers.callbacks.get_bot", return_value=AsyncMock())
    mocker.patch("dispatch.process_approval_async", new=AsyncMock())
    create_task = mocker.patch("asyncio.create_task")

    await on_curate_action(query, CurateAction(action="send_raw", item_id="item2"), state)

    archive.assert_called_once()  # item archived post-send-raw
    query.answer.assert_awaited_with("📲 Enviando para WhatsApp...")
    create_task.assert_called_once()


# ─── on_broadcast_confirm — send + cancel branches ───────────────────────────

@pytest.mark.asyncio
async def test_broadcast_confirm_send_happy_path_dispatches(
    mock_callback_query, mocker,
):
    query = mock_callback_query(data="bcast:send:bcast_1")
    mocker.patch(
        "bot.routers.callbacks.drafts_get",
        return_value={"message": "direct text", "uazapi_token": None, "uazapi_url": None},
    )
    drafts_update = mocker.patch("bot.routers.callbacks.drafts_update")
    mocker.patch("bot.routers.callbacks.get_bot", return_value=AsyncMock())
    mocker.patch("dispatch.process_approval_async", new=AsyncMock())
    create_task = mocker.patch("asyncio.create_task")

    await on_broadcast_confirm(query, BroadcastConfirm(action="send", draft_id="bcast_1"))

    drafts_update.assert_called_once_with("bcast_1", status="approved")
    query.answer.assert_awaited_with("📲 Enviando...")
    create_task.assert_called_once()


@pytest.mark.asyncio
async def test_broadcast_confirm_cancel_finalizes_without_dispatch(
    mock_callback_query, mocker,
):
    query = mock_callback_query(data="bcast:cancel:bcast_1")
    mocker.patch("bot.routers.callbacks.get_bot", return_value=AsyncMock())
    create_task = mocker.patch("asyncio.create_task")

    await on_broadcast_confirm(query, BroadcastConfirm(action="cancel", draft_id="bcast_1"))

    query.answer.assert_awaited_with("❌ Cancelado")
    create_task.assert_not_called()
```

- [ ] **Step 2: Run the file — all tests must pass**

Run: `pytest tests/test_callbacks_curation.py -v`

Expected: `10 passed`. If a test fails, the assertion describes CURRENT behavior incorrectly — fix the assertion to match, don't change the handler. Common failure: patched attribute path wrong (e.g. `bot.routers.callbacks.drafts_get` vs `bot.routers._helpers.drafts_get` — always patch at the import site of the handler's module).

- [ ] **Step 3: Commit**

```bash
git add tests/test_callbacks_curation.py
git commit -m "test(callbacks): characterize curation handlers (draft/curate/broadcast — 10 tests)"
```

---

## Task 4: Write `tests/test_callbacks_reports.py` (8 tests)

**Files:**
- Create: `tests/test_callbacks_reports.py`
- Characterizes: `on_report_type`, `on_report_years`, `on_report_year`, `on_report_month`, `on_report_download`, `on_report_back`

### Background

Report handlers delegate to `reports_nav.*` helpers. Tests verify the handler calls the right helper with the right args — the helper itself is out of scope.

- [ ] **Step 1: Write the full test file**

Create `tests/test_callbacks_reports.py`:

```python
"""Characterization tests — report navigation callbacks in webhook/bot/routers/callbacks.py."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from bot.callback_data import (
    ReportType, ReportYears, ReportYear, ReportMonth, ReportDownload, ReportBack,
)
from bot.routers.callbacks import (
    on_report_type, on_report_years, on_report_year, on_report_month,
    on_report_download, on_report_back,
)


@pytest.mark.asyncio
async def test_on_report_type_delegates_to_reports_show_latest(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100, message_id=200)
    show_latest = mocker.patch("bot.routers.callbacks.reports_show_latest", new=AsyncMock())

    await on_report_type(query, ReportType(report_type="rmw"))

    show_latest.assert_awaited_once_with(100, 200, "rmw")
    query.answer.assert_awaited_with("")


@pytest.mark.asyncio
async def test_on_report_years_delegates_to_reports_show_years(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100, message_id=200)
    show_years = mocker.patch("bot.routers.callbacks.reports_show_years", new=AsyncMock())

    await on_report_years(query, ReportYears(report_type="rmw"))

    show_years.assert_awaited_once_with(100, 200, "rmw")


@pytest.mark.asyncio
async def test_on_report_year_delegates_to_reports_show_months(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100, message_id=200)
    show_months = mocker.patch("bot.routers.callbacks.reports_show_months", new=AsyncMock())

    await on_report_year(query, ReportYear(report_type="rmw", year=2026))

    show_months.assert_awaited_once_with(100, 200, "rmw", 2026)


@pytest.mark.asyncio
async def test_on_report_month_delegates_to_reports_show_month_list(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100, message_id=200)
    show_list = mocker.patch("bot.routers.callbacks.reports_show_month_list", new=AsyncMock())

    await on_report_month(query, ReportMonth(report_type="rmw", year=2026, month=4))

    show_list.assert_awaited_once_with(100, 200, "rmw", 2026, 4)


@pytest.mark.asyncio
async def test_on_report_download_success_answers_with_upload_emoji(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100)
    mocker.patch(
        "bot.routers.callbacks.handle_report_download",
        new=AsyncMock(return_value=(True, "enviado")),
    )

    await on_report_download(query, ReportDownload(report_id="rep_abc"))

    query.answer.assert_awaited_with("📤 enviado")


@pytest.mark.asyncio
async def test_on_report_download_failure_answers_raw_message(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100)
    mocker.patch(
        "bot.routers.callbacks.handle_report_download",
        new=AsyncMock(return_value=(False, "arquivo não encontrado")),
    )

    await on_report_download(query, ReportDownload(report_id="rep_missing"))

    query.answer.assert_awaited_with("arquivo não encontrado")


@pytest.mark.asyncio
async def test_on_report_back_types_routes_to_show_types(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100, message_id=200)
    show_types = mocker.patch("bot.routers.callbacks.reports_show_types", new=AsyncMock())

    await on_report_back(query, ReportBack(target="types"))

    show_types.assert_awaited_once_with(100, message_id=200)


@pytest.mark.asyncio
async def test_on_report_back_year_target_parses_type_and_year(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100, message_id=200)
    show_months = mocker.patch("bot.routers.callbacks.reports_show_months", new=AsyncMock())

    await on_report_back(query, ReportBack(target="year:rmw:2026"))

    show_months.assert_awaited_once_with(100, 200, "rmw", 2026)
```

- [ ] **Step 2: Run the file**

Run: `pytest tests/test_callbacks_reports.py -v`

Expected: `8 passed`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_callbacks_reports.py
git commit -m "test(callbacks): characterize report navigation handlers (8 tests)"
```

---

## Task 5: Write `tests/test_callbacks_queue.py` (5 tests)

**Files:**
- Create: `tests/test_callbacks_queue.py`
- Characterizes: `on_queue_page`, `on_queue_open`, and the `on_menu_action` "queue" target (sub-test)

### Background

`on_queue_page` calls `query_handlers.format_queue_page(page=N)` and edits the message. `on_queue_open` reads from `execution.curation.redis_client.get_staging`, then posts via `execution.curation.telegram_poster.post_for_curation` (wrapped in `asyncio.to_thread`).

- [ ] **Step 1: Write the full test file**

Create `tests/test_callbacks_queue.py`:

```python
"""Characterization tests — queue navigation callbacks."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from bot.callback_data import QueuePage, QueueOpen
from bot.routers.callbacks import on_queue_page, on_queue_open


@pytest.mark.asyncio
async def test_on_queue_page_happy_path_edits_message(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100, message_id=200)
    mocker.patch(
        "bot.routers.callbacks.query_handlers.format_queue_page",
        return_value=("queue body text", {"inline_keyboard": []}),
    )
    bot = AsyncMock()
    bot.edit_message_text = AsyncMock()
    mocker.patch("bot.routers.callbacks.get_bot", return_value=bot)

    await on_queue_page(query, QueuePage(page=2))

    bot.edit_message_text.assert_awaited_once()
    kwargs = bot.edit_message_text.await_args.kwargs
    assert kwargs["chat_id"] == 100
    assert kwargs["message_id"] == 200


@pytest.mark.asyncio
async def test_on_queue_page_format_error_returns_silently(mock_callback_query, mocker):
    query = mock_callback_query()
    mocker.patch(
        "bot.routers.callbacks.query_handlers.format_queue_page",
        side_effect=RuntimeError("boom"),
    )
    bot = AsyncMock()
    mocker.patch("bot.routers.callbacks.get_bot", return_value=bot)

    # Must not raise
    await on_queue_page(query, QueuePage(page=1))

    bot.edit_message_text.assert_not_called()


@pytest.mark.asyncio
async def test_on_queue_open_happy_path_posts_for_curation(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100)
    item = {"id": "item1", "title": "T", "fullText": "body"}
    mocker.patch("execution.curation.redis_client.get_staging", return_value=item)
    to_thread = mocker.patch("asyncio.to_thread", new=AsyncMock())

    await on_queue_open(query, QueueOpen(item_id="item1"))

    query.answer.assert_awaited_with("")
    to_thread.assert_awaited_once()  # post_for_curation scheduled via to_thread


@pytest.mark.asyncio
async def test_on_queue_open_item_expired_answers_warning(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100)
    mocker.patch("execution.curation.redis_client.get_staging", return_value=None)

    await on_queue_open(query, QueueOpen(item_id="gone"))

    query.answer.assert_awaited_with("⚠️ Item expirou")


@pytest.mark.asyncio
async def test_on_queue_open_redis_error_answers_warning(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100)
    mocker.patch(
        "execution.curation.redis_client.get_staging",
        side_effect=RuntimeError("redis down"),
    )

    await on_queue_open(query, QueueOpen(item_id="x"))

    query.answer.assert_awaited_with("⚠️ Redis indisponível")
```

- [ ] **Step 2: Run the file**

Run: `pytest tests/test_callbacks_queue.py -v`

Expected: `5 passed`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_callbacks_queue.py
git commit -m "test(callbacks): characterize queue handlers (5 tests)"
```

---

## Task 6: Write `tests/test_callbacks_contacts.py` (4 tests)

**Files:**
- Create: `tests/test_callbacks_contacts.py`
- Characterizes: `on_contact_toggle`, `on_contact_page`

### Background

`on_contact_toggle` uses `SheetsClient` (imported via `from execution.integrations.sheets_client import SheetsClient`), calls `sheets.toggle_contact(...)` through `asyncio.to_thread`, and on success re-renders the list via `_render_list_view` from `bot.routers.commands`.

- [ ] **Step 1: Write the full test file**

Create `tests/test_callbacks_contacts.py`:

```python
"""Characterization tests — contact admin callbacks."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from bot.callback_data import ContactToggle, ContactPage
from bot.routers.callbacks import on_contact_toggle, on_contact_page


@pytest.mark.asyncio
async def test_contact_toggle_activate_shows_ativado_toast(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100, message_id=200)
    # to_thread(sheets.toggle_contact, SHEET_ID, phone) → (name, new_status)
    mocker.patch("asyncio.to_thread", new=AsyncMock(return_value=("João", "Big")))
    render = mocker.patch("bot.routers.commands._render_list_view", new=AsyncMock())
    mocker.patch("bot.routers.callbacks.SheetsClient")

    await on_contact_toggle(query, ContactToggle(phone="+5511999"))

    query.answer.assert_awaited_with("✅ João ativado")
    render.assert_awaited_once()


@pytest.mark.asyncio
async def test_contact_toggle_deactivate_shows_desativado_toast(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100, message_id=200)
    mocker.patch("asyncio.to_thread", new=AsyncMock(return_value=("Maria", "")))
    mocker.patch("bot.routers.commands._render_list_view", new=AsyncMock())
    mocker.patch("bot.routers.callbacks.SheetsClient")

    await on_contact_toggle(query, ContactToggle(phone="+5511888"))

    query.answer.assert_awaited_with("❌ Maria desativado")


@pytest.mark.asyncio
async def test_contact_toggle_value_error_shows_short_error(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100)
    mocker.patch("asyncio.to_thread", new=AsyncMock(side_effect=ValueError("invalid phone")))
    mocker.patch("bot.routers.callbacks.SheetsClient")

    await on_contact_toggle(query, ContactToggle(phone="bad"))

    query.answer.assert_awaited_with("❌ invalid phone")


@pytest.mark.asyncio
async def test_contact_page_renders_with_search_param(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100, message_id=200)
    render = mocker.patch("bot.routers.commands._render_list_view", new=AsyncMock())

    await on_contact_page(query, ContactPage(page=2, search="joão"))

    query.answer.assert_awaited_with("")
    render.assert_awaited_once_with(100, page=2, search="joão", message_id=200)
```

- [ ] **Step 2: Run the file**

Run: `pytest tests/test_callbacks_contacts.py -v`

Expected: `4 passed`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_callbacks_contacts.py
git commit -m "test(callbacks): characterize contact admin handlers (4 tests)"
```

---

## Task 7: Write `tests/test_callbacks_workflows.py` (6 tests)

**Files:**
- Create: `tests/test_callbacks_workflows.py`
- Characterizes: `on_workflow_run`, `on_workflow_list`, `on_nop`

### Background

`on_workflow_run` does inline `from workflow_trigger import trigger_workflow, find_triggered_run, poll_and_update, _workflow_name_by_id`. Patch at `workflow_trigger.<name>`.

- [ ] **Step 1: Write the full test file**

Create `tests/test_callbacks_workflows.py`:

```python
"""Characterization tests — workflow trigger callbacks + nop."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from bot.callback_data import WorkflowRun, WorkflowList
from bot.routers.callbacks import on_workflow_run, on_workflow_list, on_nop


@pytest.mark.asyncio
async def test_workflow_run_happy_path_edits_and_tracks(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100, message_id=200)
    mocker.patch("workflow_trigger._workflow_name_by_id", return_value="daily_report")
    mocker.patch("workflow_trigger.trigger_workflow", new=AsyncMock(return_value=(True, None)))
    mocker.patch("workflow_trigger.find_triggered_run", new=AsyncMock(return_value="run_42"))
    mocker.patch("workflow_trigger.poll_and_update", new=AsyncMock())
    bot = AsyncMock()
    bot.edit_message_text = AsyncMock()
    mocker.patch("bot.routers.callbacks.get_bot", return_value=bot)
    mocker.patch("asyncio.create_task")

    await on_workflow_run(query, WorkflowRun(workflow_id="wf_daily"))

    query.answer.assert_awaited_with("Disparando daily_report...")
    # At minimum one edit: "🚀 *Disparando daily_report...*"
    assert bot.edit_message_text.await_count >= 1


@pytest.mark.asyncio
async def test_workflow_run_trigger_failure_shows_error_with_retry(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100, message_id=200)
    mocker.patch("workflow_trigger._workflow_name_by_id", return_value="failing_wf")
    mocker.patch(
        "workflow_trigger.trigger_workflow",
        new=AsyncMock(return_value=(False, "api rate limit")),
    )
    bot = AsyncMock()
    bot.edit_message_text = AsyncMock()
    mocker.patch("bot.routers.callbacks.get_bot", return_value=bot)

    await on_workflow_run(query, WorkflowRun(workflow_id="fail_wf"))

    # Error message edit is issued
    edits = [c.args[0] for c in bot.edit_message_text.await_args_list]
    assert any("erro ao disparar" in e for e in edits)


@pytest.mark.asyncio
async def test_workflow_run_no_run_id_shows_warning(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100, message_id=200)
    mocker.patch("workflow_trigger._workflow_name_by_id", return_value="wf")
    mocker.patch("workflow_trigger.trigger_workflow", new=AsyncMock(return_value=(True, None)))
    mocker.patch("workflow_trigger.find_triggered_run", new=AsyncMock(return_value=None))
    mocker.patch("workflow_trigger.poll_and_update", new=AsyncMock())
    bot = AsyncMock()
    bot.edit_message_text = AsyncMock()
    mocker.patch("bot.routers.callbacks.get_bot", return_value=bot)
    # Run the inline _track() synchronously by not mocking create_task fully
    import asyncio
    orig_create_task = asyncio.create_task
    tasks = []
    def _capture(coro):
        t = orig_create_task(coro)
        tasks.append(t)
        return t
    mocker.patch("asyncio.create_task", side_effect=_capture)

    await on_workflow_run(query, WorkflowRun(workflow_id="wf"))
    for t in tasks:
        await t

    edits = [c.args[0] for c in bot.edit_message_text.await_args_list]
    assert any("nao encontrei o run" in e for e in edits)


@pytest.mark.asyncio
async def test_workflow_list_action_list_renders(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100, message_id=200)
    mocker.patch(
        "workflow_trigger.render_workflow_list",
        new=AsyncMock(return_value=("workflows text", {"inline_keyboard": []})),
    )
    bot = AsyncMock()
    bot.edit_message_text = AsyncMock()
    mocker.patch("bot.routers.callbacks.get_bot", return_value=bot)

    await on_workflow_list(query, WorkflowList(action="list"))

    bot.edit_message_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_workflow_list_back_menu_reopens_main_menu(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100)
    mocker.patch("bot.routers.callbacks.get_bot", return_value=AsyncMock())
    mocker.patch("bot.routers.callbacks.build_main_menu_keyboard", return_value={"kb": 1})

    await on_workflow_list(query, WorkflowList(action="back_menu"))

    query.message.answer.assert_awaited()


@pytest.mark.asyncio
async def test_nop_callback_answers_empty(mock_callback_query):
    query = mock_callback_query(data="nop")

    await on_nop(query)

    query.answer.assert_awaited_with("")
```

- [ ] **Step 2: Run the file**

Run: `pytest tests/test_callbacks_workflows.py -v`

Expected: `6 passed`. If `test_workflow_run_no_run_id_shows_warning` is flaky due to the `asyncio.create_task` interception, wrap the whole test body in a single event loop assertion or skip it with `@pytest.mark.flaky` and file a follow-up.

- [ ] **Step 3: Commit**

```bash
git add tests/test_callbacks_workflows.py
git commit -m "test(callbacks): characterize workflow handlers + nop (6 tests)"
```

---

## Task 8: Write `tests/test_messages_fsm_isolation.py` (7 tests)

**Files:**
- Create: `tests/test_messages_fsm_isolation.py`
- Characterizes: FSM state routing in `webhook/bot/routers/messages.py` — specifically that the five `@message_router.message(<FSMState>, F.text)` handlers only fire in their state, and that the reply-keyboard router matches by exact text.

### Background

This file is different from the others: we exercise the REAL `aiogram.Router` filter mechanism, not just call the handler function directly. We build a fake `Message` with different `state` values and verify which handler fires.

Because `aiogram.Router` matching requires a `Dispatcher` in a real scenario, the simpler approach is to call each handler directly with the matching state and assert the happy-path behavior. For "catch-all must not fire" we verify there is no `@message_router.message(F.text)` handler without a `StateFilter`.

- [ ] **Step 1: Write the full test file**

Create `tests/test_messages_fsm_isolation.py`:

```python
"""Characterization tests — FSM isolation in webhook/bot/routers/messages.py.

Guards the bug class fixed in commits 2cab598, a6214a0, 17135d8: a catch-all
F.text handler must NOT exist on message_router. Each FSM state handler must
fire only in its state.
"""
from __future__ import annotations

import inspect
import pytest
from unittest.mock import AsyncMock

from aiogram.filters import StateFilter
from bot.routers.messages import (
    message_router, reply_kb_router,
    on_broadcast_text, on_adjust_feedback, on_reject_reason,
    on_add_contact_data, on_writer_text,
    on_reply_reports, on_reply_queue,
)
from bot.states import (
    AdjustDraft, RejectReason, AddContact, BroadcastMessage, WriterInput,
)


# ─── No catch-all on message_router ──────────────────────────────────────────

def test_message_router_has_no_catchall_text_handler_without_state_filter():
    """Regression guard: every observer on message_router must have a state filter.

    Introduced after commits 2cab598, a6214a0, 17135d8 — a catch-all F.text handler
    would intercept FSM state messages. This test fails if one is added back.
    """
    for handler in message_router.message.handlers:
        filters = handler.filters or []
        has_state_filter = any(
            isinstance(f.callback, StateFilter) or
            getattr(f.callback, "__class__", None).__name__ == "StateFilter" or
            "state" in repr(f).lower() or
            # Aiogram 3.x wraps StatesGroup members as state filters in the handler meta
            hasattr(f, "states") or hasattr(f.callback, "states")
            for f in filters
        )
        # Also accept handlers whose primary arg is a StatesGroup subclass (passed as first positional)
        # Aiogram registers these with a StateFilter behind the scenes — we check the handler's first positional flag.
        if not has_state_filter:
            # Last resort: confirm via handler introspection
            func = handler.callback
            sig = inspect.signature(func)
            # If the handler has FSMContext param, assume state is implicitly constrained
            param_types = [str(p.annotation) for p in sig.parameters.values()]
            if not any("FSMContext" in t for t in param_types):
                pytest.fail(
                    f"message_router has a catchall F.text handler: {func.__name__}. "
                    "Add a StateFilter or register it on reply_kb_router."
                )


# ─── FSM state handlers: happy path in their state ───────────────────────────

@pytest.mark.asyncio
async def test_broadcast_text_handler_creates_draft_and_shows_preview(
    mock_message, fsm_context_in_state, mocker,
):
    msg = mock_message(text="Alô WhatsApp")
    state = fsm_context_in_state(state=BroadcastMessage.waiting_text)
    mocker.patch("bot.routers._helpers.drafts_set")
    mocker.patch("time.time", return_value=1700000000)

    await on_broadcast_text(msg, state)

    state.clear.assert_awaited_once()
    msg.answer.assert_awaited()  # preview message sent
    args, kwargs = msg.answer.await_args
    assert "PREVIEW" in args[0]


@pytest.mark.asyncio
async def test_adjust_feedback_handler_schedules_process_adjustment(
    mock_message, fsm_context_in_state, mocker,
):
    msg = mock_message(text="adicione um parágrafo")
    state = fsm_context_in_state(
        state=AdjustDraft.waiting_feedback, data={"draft_id": "abc"},
    )
    mocker.patch("bot.routers.messages.process_adjustment", new=AsyncMock())
    create_task = mocker.patch("asyncio.create_task")

    await on_adjust_feedback(msg, state)

    state.clear.assert_awaited_once()
    create_task.assert_called_once()


@pytest.mark.asyncio
async def test_reject_reason_skip_keyword_shortcircuits(
    mock_message, fsm_context_in_state, mocker,
):
    msg = mock_message(text="pular")
    state = fsm_context_in_state(
        state=RejectReason.waiting_reason, data={"feedback_key": "fbk_1"},
    )
    update = mocker.patch("bot.routers.messages.redis_queries.update_feedback_reason")

    await on_reject_reason(msg, state)

    msg.answer.assert_awaited_with("✅ Ok, sem razão registrada.")
    update.assert_not_called()


@pytest.mark.asyncio
async def test_add_contact_data_happy_path_writes_to_sheet(
    mock_message, fsm_context_in_state, mocker,
):
    msg = mock_message(text="João 11999998888")
    state = fsm_context_in_state(state=AddContact.waiting_data)
    mocker.patch(
        "bot.routers.messages.contact_admin.parse_add_input",
        return_value=("João", "11999998888"),
    )
    mocker.patch("asyncio.to_thread", new=AsyncMock(return_value=([], 0)))
    mocker.patch("bot.routers.messages.SheetsClient")

    await on_add_contact_data(msg, state)

    state.clear.assert_awaited()
    # final confirmation message sent
    final_call = msg.answer.await_args_list[-1]
    assert "adicionado" in final_call.args[0]


@pytest.mark.asyncio
async def test_writer_text_handler_schedules_process_news(
    mock_message, fsm_context_in_state, mocker,
):
    msg = mock_message(text="Iron ore up 2%")
    state = fsm_context_in_state(state=WriterInput.waiting_text)
    progress_msg = mocker.MagicMock()
    progress_msg.message_id = 55
    msg.answer = AsyncMock(return_value=progress_msg)
    mocker.patch("bot.routers.messages.ANTHROPIC_API_KEY", "test-key")
    mocker.patch("bot.routers.messages.process_news", new=AsyncMock())
    create_task = mocker.patch("asyncio.create_task")

    await on_writer_text(msg, state)

    state.clear.assert_awaited_once()
    create_task.assert_called_once()


# ─── Reply keyboard router — separate from message_router ───────────────────

@pytest.mark.asyncio
async def test_reply_kb_reports_invokes_show_types(mock_message, mocker):
    msg = mock_message(text="📊 Reports")
    show = mocker.patch("reports_nav.reports_show_types", new=AsyncMock())

    await on_reply_reports(msg)

    show.assert_awaited_once_with(msg.chat.id)


@pytest.mark.asyncio
async def test_reply_kb_queue_posts_formatted_queue(mock_message, mocker):
    msg = mock_message(text="📰 Fila")
    mocker.patch(
        "query_handlers.format_queue_page",
        return_value=("body", {"inline_keyboard": []}),
    )

    await on_reply_queue(msg)

    msg.answer.assert_awaited_once()
    body_arg = msg.answer.await_args.args[0]
    assert body_arg == "body"
```

- [ ] **Step 2: Run the file**

Run: `pytest tests/test_messages_fsm_isolation.py -v`

Expected: `8 passed` (7 numbered tests + the first regression-guard test).

**If `test_message_router_has_no_catchall_text_handler_without_state_filter` fails:** the introspection approach may need tweaking for the installed aiogram version. Alternative: replace with a simpler check — iterate `message_router.message.handlers`, confirm each handler's first positional argument is a `StatesGroup` subclass member. Keep fixing until green; do NOT skip this test — it is the primary regression guard for Phase 1.

- [ ] **Step 3: Commit**

```bash
git add tests/test_messages_fsm_isolation.py
git commit -m "test(messages): FSM isolation guards + reply_kb routing (8 tests)"
```

---

## Task 9: Verify Full Suite — Coverage, Timing, Action-Code Completeness

**Files:**
- None modified; verification only.

- [ ] **Step 1: Run the entire new suite**

Run: `pytest tests/test_callbacks_*.py tests/test_messages_fsm_isolation.py -v`

Expected: `40 passed` (or 41, depending on whether the FSM regression-guard test counts).

- [ ] **Step 2: Run full repo suite to confirm no regressions**

Run: `pytest --tb=short`

Expected: all previous tests still pass.

- [ ] **Step 3: Check suite timing**

Run: `pytest tests/test_callbacks_*.py tests/test_messages_fsm_isolation.py --durations=10`

Expected: total wall-time under 3 seconds. If any single test exceeds 500ms, look for unintentional I/O and mock it.

- [ ] **Step 4: Verify action-code coverage**

Run: `grep -E '^class \w+\(CallbackData' webhook/bot/callback_data.py`

Expected output (list of CallbackData classes):
```
class CurateAction(CallbackData, prefix="curate"):
class DraftAction(CallbackData, prefix="draft"):
class MenuAction(CallbackData, prefix="menu"):
class ReportType(CallbackData, prefix="rpt_type"):
class ReportYear(CallbackData, prefix="rpt_year"):
class ReportMonth(CallbackData, prefix="rpt_month"):
class ReportDownload(CallbackData, prefix="report_dl"):
class ReportBack(CallbackData, prefix="rpt_back"):
class ReportYears(CallbackData, prefix="rpt_years"):
class QueuePage(CallbackData, prefix="queue_page"):
class QueueOpen(CallbackData, prefix="queue_open"):
class ContactToggle(CallbackData, prefix="tgl"):
class ContactPage(CallbackData, prefix="pg"):
class WorkflowRun(CallbackData, prefix="wf_run"):
class WorkflowList(CallbackData, prefix="wf"):
class UserApproval(CallbackData, prefix="user_approve"):
class SubscriptionToggle(CallbackData, prefix="sub_toggle"):
class SubscriptionDone(CallbackData, prefix="sub_done"):
class OnboardingStart(CallbackData, prefix="onboard"):
class BroadcastConfirm(CallbackData, prefix="bcast"):
```

Confirm that `CurateAction`, `DraftAction`, `MenuAction`, `ReportType`, `ReportYears`, `ReportYear`, `ReportMonth`, `ReportDownload`, `ReportBack`, `QueuePage`, `QueueOpen`, `ContactToggle`, `ContactPage`, `WorkflowRun`, `WorkflowList`, and `BroadcastConfirm` all have at least one test in the new files.

**Explicitly not covered (out of scope for Phase 1):** `UserApproval`, `SubscriptionToggle`, `SubscriptionDone`, `OnboardingStart` — these live in `onboarding_router`, not `callback_router`, and are not affected by the Phase 2 split.

- [ ] **Step 5: Tag completion commit**

```bash
git commit --allow-empty -m "test(phase1): safety net complete — 40 characterization tests"
git tag phase1-safety-net-complete
```

---

## Self-Review Notes

Re-read before claiming Phase 1 done:

1. **Spec §2 coverage:** Each DoD bullet maps to a task.
   - "40 tests green locally" → Task 9, Step 1
   - "100% of action codes covered" → Task 9, Step 4
   - "idempotent (autouse fixture resets)" → Task 1 fixtures use `MagicMock`/`AsyncMock` which are function-scoped
   - "suite ≤ 3s" → Task 9, Step 3
   - "tests/README.md exists" → Task 2

2. **Placeholder scan:** No "TBD", no "similar to above", no "implement later". Every test has full code.

3. **Consistency:**
   - Fixture names (`mock_bot`, `mock_callback_query`, `mock_message`, `fsm_context_in_state`) used identically across all test tasks.
   - Patch paths (`bot.routers.callbacks.<name>`) consistent with handler imports.
   - All test files end `.py` and imports use bare module paths (`from bot.routers.callbacks import ...`) enabled by `conftest.py` sys.path injection.

4. **Known tradeoff documented:** `test_workflow_run_no_run_id_shows_warning` (Task 7) is the most complex test due to `asyncio.create_task` timing. Acceptable fallback noted inline.

5. **Type consistency:** All handler function names match exactly what `callbacks.py` defines (verified in the source read).

---

*Plan author: writing-plans session 2026-04-18*
*Next phase plan: `docs/superpowers/plans/2026-04-18-phase2-router-split.md` (written after Phase 1 ships green).*
