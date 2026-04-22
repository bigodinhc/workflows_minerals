# Phase 1: Flask → Aiogram 3 Migration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Flask + raw-requests Telegram bot with Aiogram 3 + aiohttp, converting all state to FSM/Redis, all threading to asyncio, and all callback parsing to CallbackData factories — zero feature regression.

**Architecture:** New `webhook/bot/` package with Aiogram routers, FSM states, CallbackData factories, and auth middleware. Existing business logic modules (`pipeline.py`, `dispatch.py`, etc.) are adapted to async in-place. Plain aiohttp routes handle non-Telegram endpoints (`/store-draft`, `/seen-articles`, `/health`, `/preview`). Entry point moves from `app:app` (Flask+gunicorn) to `python -m webhook.bot.main` (aiohttp).

**Tech Stack:** Aiogram 3.4+, aiohttp 3.9+, aiohttp-jinja2, Redis (FSM storage), anthropic AsyncAnthropic, Python 3.11

---

## File Map

### New files (create)

| File | Responsibility |
|------|---------------|
| `webhook/bot/__init__.py` | Package init |
| `webhook/bot/main.py` | Entry point: aiohttp app, Aiogram webhook setup, background task registry |
| `webhook/bot/config.py` | Environment variables, constants, bot/dispatcher/storage init |
| `webhook/bot/states.py` | FSM StatesGroup definitions (AdjustDraft, RejectReason, AddContact, NewsInput) |
| `webhook/bot/callback_data.py` | All CallbackData factory classes |
| `webhook/bot/keyboards.py` | Inline keyboard builder functions |
| `webhook/bot/middlewares/__init__.py` | Package init |
| `webhook/bot/middlewares/auth.py` | AdminAuthMiddleware |
| `webhook/bot/routers/__init__.py` | Package init |
| `webhook/bot/routers/commands.py` | All slash-command handlers |
| `webhook/bot/routers/callbacks.py` | All callback query handlers |
| `webhook/bot/routers/messages.py` | FSM text input handlers |
| `webhook/routes/__init__.py` | Package init |
| `webhook/routes/api.py` | aiohttp routes: /store-draft, /seen-articles, /health, /admin/register-commands |
| `webhook/routes/preview.py` | aiohttp route: /preview/{item_id} with aiohttp-jinja2 |
| `tests/test_bot_states.py` | FSM state tests |
| `tests/test_bot_callback_data.py` | CallbackData serialization tests |
| `tests/test_bot_middlewares.py` | Auth middleware tests |
| `tests/test_bot_commands.py` | Command handler tests |
| `tests/test_bot_callbacks.py` | Callback handler tests |
| `tests/test_bot_messages.py` | FSM message handler tests |
| `tests/test_routes_api.py` | aiohttp route tests |

### Modified files (adapt to async)

| File | Changes |
|------|---------|
| `webhook/pipeline.py` | `Anthropic` → `AsyncAnthropic`, all functions → `async def` |
| `webhook/dispatch.py` | `requests` → `aiohttp.ClientSession`, all functions → `async def` |
| `webhook/workflow_trigger.py` | `requests` → `aiohttp.ClientSession`, remove `flask.jsonify`, remove `threading`, all functions → `async def` |
| `webhook/reports_nav.py` | Wrap Supabase with `asyncio.to_thread()`, accept bot object instead of importing telegram.py |
| `webhook/contact_admin.py` | No changes needed (pure logic, no I/O) |
| `webhook/requirements.txt` | Remove flask/gunicorn, add aiogram/aiohttp/aiohttp-jinja2 |
| `Dockerfile` | Change CMD to `python -m webhook.bot.main` |
| `railway.json` | Change startCommand to `python -m webhook.bot.main` |

### Deleted files

| File | Replaced by |
|------|------------|
| `webhook/app.py` | `webhook/bot/main.py` + `webhook/routes/api.py` + `webhook/routes/preview.py` |
| `webhook/telegram.py` | Aiogram SDK (bot.send_message, etc.) |
| `webhook/callback_router.py` | `webhook/bot/routers/callbacks.py` |

### Unchanged files (no modifications needed)

- `webhook/status_builder.py`
- `webhook/query_handlers.py`
- `webhook/redis_queries.py`
- `webhook/digest.py`
- `webhook/templates/preview.html`
- All `execution/` files
- All `.github/workflows/` files
- All `dashboard/` files
- All `actors/` files

---

## Task 1: Dependencies & Config

**Files:**
- Modify: `webhook/requirements.txt`
- Create: `webhook/bot/__init__.py`
- Create: `webhook/bot/config.py`

- [ ] **Step 1: Update webhook/requirements.txt**

Replace the contents of `webhook/requirements.txt`:

```
aiogram>=3.4.0,<4.0
aiohttp>=3.9.0,<4.0
aiohttp-jinja2>=1.6,<2.0
requests>=2.28.0
gspread>=5.0.0
google-auth>=2.0.0
anthropic>=0.40.0
redis>=5.0,<6.0
pyyaml>=6.0,<7.0
croniter>=2.0,<3.0
supabase>=2.0.0,<3.0
```

- [ ] **Step 2: Install new dependencies locally**

Run: `cd webhook && pip install -r requirements.txt`

- [ ] **Step 3: Create webhook/bot/__init__.py**

```python
"""Aiogram 3 bot package for SuperMustache Minerals Trading."""
```

- [ ] **Step 4: Create webhook/bot/config.py**

```python
"""Environment variables, constants, and shared bot/dispatcher/storage instances."""

import os
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage

logger = logging.getLogger(__name__)

# ── Environment ──

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
REDIS_URL = os.getenv("REDIS_URL", "")
ANTHROPIC_API_KEY = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
UAZAPI_URL = os.getenv("UAZAPI_URL", "https://mineralstrading.uazapi.com")
UAZAPI_TOKEN = (os.getenv("UAZAPI_TOKEN") or "").strip()
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
SHEET_ID = "1tU3Izdo21JichTXg15bc1paWUiN8XioJYZUPpbIUgL0"
WEBHOOK_PATH = "/webhook"
WEBAPP_HOST = "0.0.0.0"
WEBAPP_PORT = int(os.getenv("PORT", 8080))
TELEGRAM_WEBHOOK_URL = os.getenv("TELEGRAM_WEBHOOK_URL", "").rstrip("/")

# ── Singletons (lazy) ──

_bot: Bot | None = None
_dp: Dispatcher | None = None
_storage: RedisStorage | None = None


def get_storage() -> RedisStorage:
    global _storage
    if _storage is None:
        _storage = RedisStorage.from_url(REDIS_URL)
    return _storage


def get_bot() -> Bot:
    global _bot
    if _bot is None:
        _bot = Bot(
            token=TELEGRAM_BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
        )
    return _bot


def get_dispatcher() -> Dispatcher:
    global _dp
    if _dp is None:
        _dp = Dispatcher(storage=get_storage())
    return _dp
```

- [ ] **Step 5: Verify import works**

Run: `cd .. && python -c "from webhook.bot.config import get_bot; print('ok')"`
Expected: `ok`

- [ ] **Step 6: Commit**

```bash
git add webhook/requirements.txt webhook/bot/__init__.py webhook/bot/config.py
git commit -m "feat(bot): add Aiogram 3 dependencies and config module"
```

---

## Task 2: FSM States

**Files:**
- Create: `webhook/bot/states.py`
- Test: `tests/test_bot_states.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_bot_states.py`:

```python
"""Tests for FSM state definitions."""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

from aiogram.fsm.state import StatesGroup, State
from bot.states import AdjustDraft, RejectReason, AddContact, NewsInput


def test_adjust_draft_has_waiting_feedback():
    assert hasattr(AdjustDraft, "waiting_feedback")
    assert isinstance(AdjustDraft.waiting_feedback, State)


def test_reject_reason_has_waiting_reason():
    assert hasattr(RejectReason, "waiting_reason")
    assert isinstance(RejectReason.waiting_reason, State)


def test_add_contact_has_waiting_data():
    assert hasattr(AddContact, "waiting_data")
    assert isinstance(AddContact.waiting_data, State)


def test_news_input_has_processing():
    assert hasattr(NewsInput, "processing")
    assert isinstance(NewsInput.processing, State)


def test_all_are_states_groups():
    for cls in (AdjustDraft, RejectReason, AddContact, NewsInput):
        assert issubclass(cls, StatesGroup)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bot_states.py -v`
Expected: FAIL (ModuleNotFoundError: No module named 'bot.states')

- [ ] **Step 3: Create webhook/bot/states.py**

```python
"""FSM StatesGroup definitions.

Replace in-memory dicts (ADJUST_STATE, REJECT_REASON_STATE, ADMIN_STATE)
with Aiogram FSM backed by RedisStorage — state survives redeploys.
"""

from aiogram.fsm.state import State, StatesGroup


class AdjustDraft(StatesGroup):
    """User pressed 'Ajustar' and must send feedback text."""
    waiting_feedback = State()


class RejectReason(StatesGroup):
    """User pressed 'Rejeitar' — optionally sends a reason."""
    waiting_reason = State()


class AddContact(StatesGroup):
    """User typed /add — must send 'Nome Telefone'."""
    waiting_data = State()


class NewsInput(StatesGroup):
    """Guard: text is being processed by the 3-agent pipeline."""
    processing = State()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_bot_states.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add webhook/bot/states.py tests/test_bot_states.py
git commit -m "feat(bot): add FSM state definitions for adjust, reject, add-contact, news"
```

---

## Task 3: CallbackData Factories

**Files:**
- Create: `webhook/bot/callback_data.py`
- Test: `tests/test_bot_callback_data.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_bot_callback_data.py`:

```python
"""Tests for CallbackData factory serialization/deserialization."""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

import pytest
from bot.callback_data import (
    CurateAction, DraftAction, MenuAction,
    ReportType, ReportYear, ReportMonth, ReportDownload, ReportBack, ReportYears,
    QueuePage, QueueOpen,
    ContactToggle, ContactPage,
    WorkflowRun, WorkflowList,
)


def test_curate_action_pack_unpack():
    cb = CurateAction(action="archive", item_id="abc123")
    packed = cb.pack()
    assert packed.startswith("curate:")
    parsed = CurateAction.unpack(packed)
    assert parsed.action == "archive"
    assert parsed.item_id == "abc123"


def test_draft_action_pack_unpack():
    cb = DraftAction(action="approve", draft_id="news_12345")
    packed = cb.pack()
    assert packed.startswith("draft:")
    parsed = DraftAction.unpack(packed)
    assert parsed.action == "approve"
    assert parsed.draft_id == "news_12345"


def test_menu_action_pack_unpack():
    cb = MenuAction(target="reports")
    packed = cb.pack()
    assert packed.startswith("menu:")
    parsed = MenuAction.unpack(packed)
    assert parsed.target == "reports"


def test_report_type_pack_unpack():
    cb = ReportType(report_type="Market Reports")
    packed = cb.pack()
    parsed = ReportType.unpack(packed)
    assert parsed.report_type == "Market Reports"


def test_report_year_pack_unpack():
    cb = ReportYear(report_type="Market Reports", year=2026)
    packed = cb.pack()
    parsed = ReportYear.unpack(packed)
    assert parsed.report_type == "Market Reports"
    assert parsed.year == 2026


def test_report_month_pack_unpack():
    cb = ReportMonth(report_type="Research Reports", year=2025, month=11)
    packed = cb.pack()
    parsed = ReportMonth.unpack(packed)
    assert parsed.report_type == "Research Reports"
    assert parsed.year == 2025
    assert parsed.month == 11


def test_report_download_pack_unpack():
    cb = ReportDownload(report_id="uuid-abc")
    packed = cb.pack()
    parsed = ReportDownload.unpack(packed)
    assert parsed.report_id == "uuid-abc"


def test_report_back_pack_unpack():
    cb = ReportBack(target="types")
    packed = cb.pack()
    parsed = ReportBack.unpack(packed)
    assert parsed.target == "types"


def test_report_years_pack_unpack():
    cb = ReportYears(report_type="Market Reports")
    packed = cb.pack()
    parsed = ReportYears.unpack(packed)
    assert parsed.report_type == "Market Reports"


def test_queue_page_pack_unpack():
    cb = QueuePage(page=3)
    packed = cb.pack()
    parsed = QueuePage.unpack(packed)
    assert parsed.page == 3


def test_queue_open_pack_unpack():
    cb = QueueOpen(item_id="platts-xyz")
    packed = cb.pack()
    parsed = QueueOpen.unpack(packed)
    assert parsed.item_id == "platts-xyz"


def test_contact_toggle_pack_unpack():
    cb = ContactToggle(phone="5511999999999")
    packed = cb.pack()
    parsed = ContactToggle.unpack(packed)
    assert parsed.phone == "5511999999999"


def test_contact_page_pack_unpack():
    cb = ContactPage(page=2, search="joao")
    packed = cb.pack()
    parsed = ContactPage.unpack(packed)
    assert parsed.page == 2
    assert parsed.search == "joao"


def test_contact_page_no_search():
    cb = ContactPage(page=1)
    packed = cb.pack()
    parsed = ContactPage.unpack(packed)
    assert parsed.page == 1
    assert parsed.search == ""


def test_workflow_run_pack_unpack():
    cb = WorkflowRun(workflow_id="morning_check.yml")
    packed = cb.pack()
    parsed = WorkflowRun.unpack(packed)
    assert parsed.workflow_id == "morning_check.yml"


def test_workflow_list_pack_unpack():
    cb = WorkflowList(action="list")
    packed = cb.pack()
    parsed = WorkflowList.unpack(packed)
    assert parsed.action == "list"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bot_callback_data.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Create webhook/bot/callback_data.py**

```python
"""CallbackData factory definitions.

Replace manual string parsing (callback_data.split(':', 1)) with typed
factories that serialize/deserialize automatically.
"""

from aiogram.filters.callback_data import CallbackData


class CurateAction(CallbackData, prefix="curate"):
    action: str  # archive, reject, pipeline
    item_id: str


class DraftAction(CallbackData, prefix="draft"):
    action: str  # approve, test_approve, adjust, reject
    draft_id: str


class MenuAction(CallbackData, prefix="menu"):
    target: str  # reports, queue, history, rejections, stats, status, etc.


class ReportType(CallbackData, prefix="rpt_type"):
    report_type: str


class ReportYear(CallbackData, prefix="rpt_year"):
    report_type: str
    year: int


class ReportMonth(CallbackData, prefix="rpt_month"):
    report_type: str
    year: int
    month: int


class ReportDownload(CallbackData, prefix="report_dl"):
    report_id: str


class ReportBack(CallbackData, prefix="rpt_back"):
    target: str  # types, type:<name>, years:<name>, year:<name>:<year>


class ReportYears(CallbackData, prefix="rpt_years"):
    report_type: str


class QueuePage(CallbackData, prefix="queue_page"):
    page: int


class QueueOpen(CallbackData, prefix="queue_open"):
    item_id: str


class ContactToggle(CallbackData, prefix="tgl"):
    phone: str


class ContactPage(CallbackData, prefix="pg"):
    page: int
    search: str = ""


class WorkflowRun(CallbackData, prefix="wf_run"):
    workflow_id: str


class WorkflowList(CallbackData, prefix="wf"):
    action: str  # list, back_menu
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_bot_callback_data.py -v`
Expected: 17 passed

- [ ] **Step 5: Commit**

```bash
git add webhook/bot/callback_data.py tests/test_bot_callback_data.py
git commit -m "feat(bot): add CallbackData factories for all callback types"
```

---

## Task 4: Auth Middleware

**Files:**
- Create: `webhook/bot/middlewares/__init__.py`
- Create: `webhook/bot/middlewares/auth.py`
- Test: `tests/test_bot_middlewares.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_bot_middlewares.py`:

```python
"""Tests for AdminAuthMiddleware."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

import pytest
from bot.middlewares.auth import AdminAuthMiddleware


@pytest.fixture
def middleware():
    return AdminAuthMiddleware()


def _make_event(user_id):
    event = MagicMock()
    event.from_user = MagicMock()
    event.from_user.id = user_id
    return event


@pytest.mark.asyncio
async def test_authorized_user_passes_through(middleware):
    handler = AsyncMock(return_value="result")
    event = _make_event(12345)
    with patch("bot.middlewares.auth.contact_admin") as mock_ca:
        mock_ca.is_authorized.return_value = True
        result = await middleware(handler, event, {})
    assert result == "result"
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_unauthorized_user_blocked(middleware):
    handler = AsyncMock(return_value="result")
    event = _make_event(99999)
    with patch("bot.middlewares.auth.contact_admin") as mock_ca:
        mock_ca.is_authorized.return_value = False
        result = await middleware(handler, event, {})
    assert result is None
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_event_without_from_user_passes(middleware):
    handler = AsyncMock(return_value="result")
    event = MagicMock(spec=[])  # no from_user attr
    result = await middleware(handler, event, {})
    assert result == "result"
    handler.assert_awaited_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pip install pytest-asyncio && pytest tests/test_bot_middlewares.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Create webhook/bot/middlewares/__init__.py**

```python
"""Bot middleware package."""
```

- [ ] **Step 4: Create webhook/bot/middlewares/auth.py**

```python
"""Admin authorization middleware.

Applied to admin-only routers. Silently drops updates from unauthorized users.
The /start command lives on a separate public router without this middleware.
"""

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

import contact_admin

logger = logging.getLogger(__name__)


class AdminAuthMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        from_user = getattr(event, "from_user", None)
        if from_user is None:
            return await handler(event, data)

        chat_id = from_user.id
        if not contact_admin.is_authorized(chat_id):
            logger.debug(f"Unauthorized access attempt from chat_id={chat_id}")
            return None

        return await handler(event, data)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_bot_middlewares.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add webhook/bot/middlewares/__init__.py webhook/bot/middlewares/auth.py tests/test_bot_middlewares.py
git commit -m "feat(bot): add AdminAuthMiddleware for admin-only routers"
```

---

## Task 5: Async Pipeline (pipeline.py)

**Files:**
- Modify: `webhook/pipeline.py`
- Existing test: `tests/test_prompts.py` (verify still passes)

- [ ] **Step 1: Rewrite webhook/pipeline.py to async**

Replace the full contents of `webhook/pipeline.py`:

```python
"""Claude AI 3-agent pipeline for iron ore market news processing.

Agents:
  Writer   — drafts the WhatsApp message from raw content
  Reviewer — critiques the draft for accuracy and tone
  Finalizer (Curator) — produces the final formatted message
  Adjuster — applies user feedback to an existing draft

All functions are async — they use anthropic.AsyncAnthropic.
"""

import logging

import anthropic

from bot.config import ANTHROPIC_API_KEY
from execution.core.prompts import WRITER_SYSTEM, CRITIQUE_SYSTEM, CURATOR_SYSTEM, ADJUSTER_SYSTEM

logger = logging.getLogger(__name__)


async def call_claude(system_prompt: str, user_prompt: str) -> str:
    """Call Claude API (async) and return text response."""
    try:
        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        message = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return message.content[0].text
    except anthropic.APIConnectionError as e:
        logger.error(f"Anthropic connection error: {e}")
        raise
    except anthropic.AuthenticationError as e:
        logger.error(f"Anthropic auth error (bad key?): {e}")
        raise
    except Exception as e:
        logger.error(f"Anthropic error ({type(e).__name__}): {e}")
        raise


async def run_3_agents(raw_text: str, on_phase_start=None) -> str:
    """Run Writer -> Critique -> Curator chain. Returns final formatted message.

    on_phase_start: optional callable(phase_name) invoked before each phase.
    If it is a coroutine function, it will be awaited.
    """
    import asyncio

    async def _notify(phase_name):
        if on_phase_start is None:
            return
        result = on_phase_start(phase_name)
        if asyncio.iscoroutine(result):
            await result

    await _notify("Writer")
    logger.info("Agent 1/3: Writer starting...")
    writer_output = await call_claude(
        WRITER_SYSTEM,
        f"Processe e analise o seguinte conteúdo do mercado de minério de ferro.\n\nCONTEÚDO:\n---\n{raw_text}\n---\n\nProduza sua análise completa.",
    )
    logger.info(f"Writer done ({len(writer_output)} chars)")

    await _notify("Reviewer")
    logger.info("Agent 2/3: Critique starting...")
    critique_output = await call_claude(
        CRITIQUE_SYSTEM,
        f"Revise o trabalho do Writer:\n\nTRABALHO DO WRITER:\n---\n{writer_output}\n---\n\nTEXTO ORIGINAL:\n---\n{raw_text}\n---\n\nExecute sua revisão crítica.",
    )
    logger.info(f"Critique done ({len(critique_output)} chars)")

    await _notify("Finalizer")
    logger.info("Agent 3/3: Curator starting...")
    curator_output = await call_claude(
        CURATOR_SYSTEM,
        f"Crie a versão final para WhatsApp.\n\nTEXTO DO WRITER:\n---\n{writer_output}\n---\n\nFEEDBACK DO CRITIQUE:\n---\n{critique_output}\n---\n\nTEXTO ORIGINAL:\n---\n{raw_text}\n---\n\nProduza APENAS a mensagem formatada.",
    )
    logger.info(f"Curator done ({len(curator_output)} chars)")

    return curator_output


async def run_adjuster(current_draft: str, feedback: str, original_text: str) -> str:
    """Re-run Curator with adjustment feedback."""
    logger.info("Adjuster starting...")
    adjusted = await call_claude(
        ADJUSTER_SYSTEM,
        f"MENSAGEM ATUAL:\n---\n{current_draft}\n---\n\nAJUSTES SOLICITADOS:\n---\n{feedback}\n---\n\nTEXTO ORIGINAL (referência):\n---\n{original_text}\n---\n\nAplique os ajustes e produza a mensagem final.",
    )
    logger.info(f"Adjuster done ({len(adjusted)} chars)")
    return adjusted
```

- [ ] **Step 2: Verify prompt tests still pass**

Run: `pytest tests/test_prompts.py -v`
Expected: All pass (these test prompt content, not pipeline functions)

- [ ] **Step 3: Commit**

```bash
git add webhook/pipeline.py
git commit -m "feat(pipeline): convert to async with AsyncAnthropic"
```

---

## Task 6: Async Dispatch (dispatch.py)

**Files:**
- Modify: `webhook/dispatch.py`

- [ ] **Step 1: Rewrite webhook/dispatch.py to async**

Replace the full contents of `webhook/dispatch.py`:

```python
"""WhatsApp sending + approval/test async flows.

All functions are async — they use aiohttp.ClientSession for HTTP
and Aiogram bot for Telegram messages.
"""

import json
import logging

import aiohttp

from bot.config import get_bot, UAZAPI_URL, UAZAPI_TOKEN, GOOGLE_CREDENTIALS_JSON, SHEET_ID
from execution.core.delivery_reporter import DeliveryReporter, build_contact_from_row
from execution.integrations.sheets_client import SheetsClient

logger = logging.getLogger(__name__)


# ── Google Sheets (contacts) — sync, wrapped in to_thread ──

def _get_contacts_sync():
    """Fetch WhatsApp contacts from Google Sheets (sync)."""
    import gspread
    from google.oauth2.service_account import Credentials
    import time

    creds_json = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(creds_json, scopes=[
        "https://www.googleapis.com/auth/spreadsheets.readonly",
    ])
    gc = gspread.authorize(creds)
    sheet = gc.open_by_key(SHEET_ID).sheet1

    max_retries = 3
    records = []
    for attempt in range(max_retries):
        try:
            records = sheet.get_all_records()
            break
        except Exception as e:
            if attempt == max_retries - 1:
                logger.error(f"Failed to fetch contacts after {max_retries} attempts: {e}")
                raise
            sleep_time = 2 ** attempt
            logger.warning(f"Google Sheets API error {e}. Retrying in {sleep_time}s...")
            time.sleep(sleep_time)

    contacts = [r for r in records if r.get("ButtonPayload") == "Big"]
    logger.info(f"Found {len(contacts)} contacts with ButtonPayload='Big'")
    return contacts


async def get_contacts():
    """Fetch WhatsApp contacts (async wrapper)."""
    import asyncio
    return await asyncio.to_thread(_get_contacts_sync)


# ── WhatsApp sending ──

async def send_whatsapp(phone, message, token=None, url=None):
    """Send WhatsApp message via Uazapi (async)."""
    use_token = token or UAZAPI_TOKEN
    use_url = url or UAZAPI_URL
    headers = {
        "token": use_token,
        "Content-Type": "application/json",
    }
    payload = {
        "number": str(phone),
        "text": message,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{use_url}/send/text",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"WhatsApp {phone}: HTTP {resp.status} - {body[:200]}")
                return resp.status == 200
    except Exception as e:
        logger.error(f"WhatsApp send error for {phone}: {e}")
        return False


async def _send_whatsapp_raising(phone, text, token=None, url=None):
    """Raising wrapper around send_whatsapp for DeliveryReporter contract."""
    use_token = token or UAZAPI_TOKEN
    use_url = url or UAZAPI_URL
    headers = {"token": use_token, "Content-Type": "application/json"}
    payload = {"number": str(phone), "text": text}
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{use_url}/send/text",
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()


# ── Async processing ──

async def process_approval_async(chat_id, draft_message, uazapi_token=None, uazapi_url=None):
    """Process WhatsApp sending with progress updates via DeliveryReporter."""
    import asyncio
    bot = get_bot()
    progress = await bot.send_message(chat_id, "⏳ Iniciando envio para WhatsApp...")
    progress_msg_id = progress.message_id

    try:
        raw_contacts = await get_contacts()
        delivery_contacts = [bc for c in raw_contacts if (bc := build_contact_from_row(c))]

        await bot.edit_message_text(
            f"⏳ Enviando para {len(delivery_contacts)} contatos...\n0/{len(delivery_contacts)}",
            chat_id=chat_id,
            message_id=progress_msg_id,
        )

        async def on_progress(processed, total_, result):
            if processed % 10 == 0:
                await bot.edit_message_text(
                    f"⏳ Enviando...\n{processed}/{total_} processados",
                    chat_id=chat_id,
                    message_id=progress_msg_id,
                )

        # DeliveryReporter is sync — wrap send_fn as sync too for compatibility
        def send_fn_sync(phone, text):
            loop = asyncio.get_event_loop()
            future = asyncio.ensure_future(_send_whatsapp_raising(phone, text, token=uazapi_token, url=uazapi_url))
            # Can't await inside sync callback — use to_thread pattern instead
            raise NotImplementedError("TODO: DeliveryReporter needs async adaptation in Phase 1")

        # For now, use sync requests inside to_thread for DeliveryReporter compatibility
        import requests

        def send_fn(phone, text):
            use_token = uazapi_token or UAZAPI_TOKEN
            use_url_val = uazapi_url or UAZAPI_URL
            headers_req = {"token": use_token, "Content-Type": "application/json"}
            payload_req = {"number": str(phone), "text": text}
            response = requests.post(
                f"{use_url_val}/send/text",
                json=payload_req,
                headers=headers_req,
                timeout=30,
            )
            response.raise_for_status()
            return response.json()

        def on_progress_sync(processed, total_, result):
            if processed % 10 == 0:
                asyncio.run_coroutine_threadsafe(
                    bot.edit_message_text(
                        f"⏳ Enviando...\n{processed}/{total_} processados",
                        chat_id=chat_id,
                        message_id=progress_msg_id,
                    ),
                    asyncio.get_event_loop(),
                )

        reporter = DeliveryReporter(
            workflow="webhook_approval",
            send_fn=send_fn,
            telegram_chat_id=chat_id,
            gh_run_id=None,
        )

        report = await asyncio.to_thread(
            reporter.dispatch, delivery_contacts, draft_message, on_progress_sync,
        )

        await bot.edit_message_text(
            "✔️ Envio finalizado — veja resumo detalhado abaixo.",
            chat_id=chat_id,
            message_id=progress_msg_id,
        )

        logger.info(
            f"Approval complete: {report.success_count} sent, {report.failure_count} failed"
        )

    except Exception as e:
        logger.error(f"Approval processing error: {e}")
        error_text = f"❌ ERRO NO ENVIO\n\n{str(e)}"
        try:
            await bot.edit_message_text(error_text, chat_id=chat_id, message_id=progress_msg_id)
        except Exception:
            await bot.send_message(chat_id, error_text)


async def process_test_send_async(chat_id, draft_id, draft_message, uazapi_token=None, uazapi_url=None):
    """Send message only to the first contact for testing."""
    from bot.keyboards import build_approval_keyboard
    bot = get_bot()
    try:
        contacts = await get_contacts()
        if not contacts:
            await bot.send_message(chat_id, "❌ Nenhum contato encontrado na planilha.")
            return

        first_contact = contacts[0]
        name = first_contact.get("Nome", "Contato 1")
        phone = first_contact.get("Evolution-api") or first_contact.get("Telefone")
        if not phone:
            await bot.send_message(chat_id, "❌ Primeiro contato sem telefone.")
            return

        phone = str(phone).replace("whatsapp:", "").strip()

        if await send_whatsapp(phone, draft_message, token=uazapi_token, url=uazapi_url):
            await bot.send_message(
                chat_id,
                f"🧪 *TESTE OK*\n\n"
                f"✅ Enviado para: {name} ({phone})\n\n"
                f"Se ficou bom, clique em ✅ Aprovar para enviar a todos os {len(contacts)} contatos.",
            )
            # Re-send approval buttons
            display = draft_message[:3500] if len(draft_message) > 3500 else draft_message
            await bot.send_message(
                chat_id,
                f"📋 *PREVIEW*\n\n{display}",
                reply_markup=build_approval_keyboard(draft_id),
            )
        else:
            await bot.send_message(
                chat_id,
                f"❌ *TESTE FALHOU*\n\nFalha ao enviar para: {name} ({phone})\nVerifique o token UAZAPI.",
            )

        logger.info(f"Test send for {draft_id}: {name} ({phone})")
    except Exception as e:
        logger.error(f"Test send error: {e}")
        await bot.send_message(chat_id, f"❌ Erro no teste:\n{str(e)[:500]}")
```

- [ ] **Step 2: Commit**

```bash
git add webhook/dispatch.py
git commit -m "feat(dispatch): convert to async with aiohttp + Aiogram bot"
```

---

## Task 7: Keyboards Module

**Files:**
- Create: `webhook/bot/keyboards.py`

- [ ] **Step 1: Create webhook/bot/keyboards.py**

```python
"""Inline keyboard builders.

Centralizes keyboard construction so routers stay lean.
Uses InlineKeyboardBuilder from aiogram.utils.keyboard.
"""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.callback_data import (
    DraftAction, MenuAction,
    ReportType as ReportTypeCB, ReportYears, ReportBack,
    WorkflowList,
)


def build_approval_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    """Build the 4-button approval keyboard for a draft."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Aprovar e Enviar",
            callback_data=DraftAction(action="approve", draft_id=draft_id).pack(),
        ),
        InlineKeyboardButton(
            text="🧪 Teste",
            callback_data=DraftAction(action="test_approve", draft_id=draft_id).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="✏️ Ajustar",
            callback_data=DraftAction(action="adjust", draft_id=draft_id).pack(),
        ),
        InlineKeyboardButton(
            text="❌ Rejeitar",
            callback_data=DraftAction(action="reject", draft_id=draft_id).pack(),
        ),
    )
    return builder.as_markup()


def build_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Build the /s main menu keyboard."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📊 Relatórios", callback_data=MenuAction(target="reports").pack()),
        InlineKeyboardButton(text="📰 Fila", callback_data=MenuAction(target="queue").pack()),
    )
    builder.row(
        InlineKeyboardButton(text="📜 Histórico", callback_data=MenuAction(target="history").pack()),
        InlineKeyboardButton(text="❌ Recusados", callback_data=MenuAction(target="rejections").pack()),
    )
    builder.row(
        InlineKeyboardButton(text="📈 Stats", callback_data=MenuAction(target="stats").pack()),
        InlineKeyboardButton(text="🔄 Status", callback_data=MenuAction(target="status").pack()),
    )
    builder.row(
        InlineKeyboardButton(text="🔁 Reprocessar", callback_data=MenuAction(target="reprocess").pack()),
        InlineKeyboardButton(text="📋 Contatos", callback_data=MenuAction(target="list").pack()),
    )
    builder.row(
        InlineKeyboardButton(text="➕ Add Contato", callback_data=MenuAction(target="add").pack()),
    )
    builder.row(
        InlineKeyboardButton(text="⚡ Workflows", callback_data=WorkflowList(action="list").pack()),
        InlineKeyboardButton(text="❓ Help", callback_data=MenuAction(target="help").pack()),
    )
    return builder.as_markup()


def build_report_types_keyboard() -> InlineKeyboardMarkup:
    """Build the report category selection keyboard."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="📊 Market Reports",
        callback_data=ReportTypeCB(report_type="Market Reports").pack(),
    ))
    builder.row(InlineKeyboardButton(
        text="📊 Research Reports",
        callback_data=ReportTypeCB(report_type="Research Reports").pack(),
    ))
    return builder.as_markup()
```

- [ ] **Step 2: Commit**

```bash
git add webhook/bot/keyboards.py
git commit -m "feat(bot): add inline keyboard builder functions"
```

---

## Task 8: Async Workflow Trigger (workflow_trigger.py)

**Files:**
- Modify: `webhook/workflow_trigger.py`

- [ ] **Step 1: Rewrite webhook/workflow_trigger.py to async**

Replace the full contents of `webhook/workflow_trigger.py`:

```python
"""Trigger GitHub Actions workflows from Telegram with live status polling.

All functions are async — they use aiohttp for HTTP and Aiogram bot for Telegram.
"""

import asyncio
import logging
import os

import aiohttp

from bot.config import get_bot
from bot.callback_data import WorkflowRun, WorkflowList as WorkflowListCB

logger = logging.getLogger(__name__)

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_OWNER = os.getenv("GITHUB_OWNER", "bigodinhc")
GITHUB_REPO = os.getenv("GITHUB_REPO", "workflows_minerals")

WORKFLOW_CATALOG = [
    {"id": "morning_check.yml", "name": "MORNING CHECK", "description": "Precos Platts (Fines, Lump, Pellet, VIU)"},
    {"id": "baltic_ingestion.yml", "name": "BALTIC EXCHANGE", "description": "BDI + Rotas Capesize"},
    {"id": "daily_report.yml", "name": "DAILY SGX REPORT", "description": "Futuros SGX 62% Fe"},
    {"id": "market_news.yml", "name": "PLATTS INGESTION", "description": "Noticias Platts + curadoria"},
    {"id": "platts_reports.yml", "name": "PLATTS REPORTS", "description": "PDF reports scraping"},
]

_GH_API = "https://api.github.com"
_POLL_INTERVAL_SECONDS = 15
_POLL_TIMEOUT_SECONDS = 600


def _gh_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _workflow_name_by_id(workflow_id):
    for w in WORKFLOW_CATALOG:
        if w["id"] == workflow_id:
            return w["name"]
    return workflow_id


async def render_workflow_list():
    """Fetch last run per workflow, return (text, reply_markup dict)."""
    last_runs = {}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{_GH_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/actions/runs",
                headers=_gh_headers(),
                params={"per_page": 50},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for run in data.get("workflow_runs", []):
                        path = run.get("path", "")
                        wf_id = path.split("/")[-1] if "/" in path else path
                        if wf_id not in last_runs:
                            last_runs[wf_id] = run
    except Exception as exc:
        logger.error(f"render_workflow_list GitHub API error: {exc}")

    text = "⚡ *Workflows*\n\nEscolha um workflow para disparar:"
    keyboard = []
    for wf in WORKFLOW_CATALOG:
        run = last_runs.get(wf["id"])
        if run:
            conclusion = run.get("conclusion")
            if conclusion == "success":
                icon = "✅"
            elif conclusion == "failure":
                icon = "❌"
            elif run.get("status") == "in_progress":
                icon = "🔄"
            else:
                icon = "⏳"
        else:
            icon = "❓"
        label = f"{icon} {wf['name']}"
        keyboard.append([{"text": label, "callback_data": WorkflowRun(workflow_id=wf["id"]).pack()}])

    keyboard.append([{"text": "⬅ Menu", "callback_data": WorkflowListCB(action="back_menu").pack()}])
    markup = {"inline_keyboard": keyboard}
    return text, markup


async def trigger_workflow(workflow_id):
    """Dispatch a workflow run. Returns (ok, error)."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{_GH_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/actions/workflows/{workflow_id}/dispatches",
                headers=_gh_headers(),
                json={"ref": "main", "inputs": {"dry_run": "false"}},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 204:
                    return True, None
                body = await resp.text()
                return False, f"HTTP {resp.status}: {body[:100]}"
    except Exception as exc:
        logger.error(f"trigger_workflow error: {exc}")
        return False, str(exc)


async def find_triggered_run(workflow_id, max_wait=30):
    """Poll for a newly-created run. Returns run_id or None."""
    for _ in range(max_wait // 5):
        await asyncio.sleep(5)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{_GH_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/actions/workflows/{workflow_id}/runs",
                    headers=_gh_headers(),
                    params={"per_page": 1, "status": "in_progress"},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        runs = data.get("workflow_runs", [])
                        if runs:
                            return runs[0]["id"]
        except Exception as exc:
            logger.warning(f"find_triggered_run poll error: {exc}")
    return None


async def check_run_status(run_id):
    """Check a specific run. Returns (status, conclusion, html_url)."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{_GH_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/actions/runs/{run_id}",
                headers=_gh_headers(),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["status"], data.get("conclusion"), data.get("html_url", "")
    except Exception as exc:
        logger.error(f"check_run_status error: {exc}")
    return "unknown", None, ""


async def poll_and_update(chat_id, message_id, workflow_id, run_id):
    """Poll run status and edit Telegram message (async, no thread)."""
    bot = get_bot()
    name = _workflow_name_by_id(workflow_id)
    elapsed = 0

    while elapsed < _POLL_TIMEOUT_SECONDS:
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        elapsed += _POLL_INTERVAL_SECONDS

        status, conclusion, html_url = await check_run_status(run_id)

        if status == "completed":
            if conclusion == "success":
                icon, label = "✅", "concluido"
            else:
                icon, label = "❌", f"falhou ({conclusion})"

            buttons = {"inline_keyboard": [
                [{"text": "🔗 Ver no GitHub", "url": html_url}],
                [{"text": "⬅ Workflows", "callback_data": WorkflowListCB(action="list").pack()}],
            ]}
            await bot.edit_message_text(
                f"{icon} *{name}* {label}",
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=buttons,
            )
            return

    await bot.edit_message_text(
        f"⏰ *{name}* — timeout (10min)\n\nVerifique no GitHub.",
        chat_id=chat_id,
        message_id=message_id,
        reply_markup={"inline_keyboard": [[
            {"text": "⬅ Workflows", "callback_data": WorkflowListCB(action="list").pack()},
        ]]},
    )
```

- [ ] **Step 2: Commit**

```bash
git add webhook/workflow_trigger.py
git commit -m "feat(workflow_trigger): convert to async with aiohttp"
```

---

## Task 9: Async Reports Nav (reports_nav.py)

**Files:**
- Modify: `webhook/reports_nav.py`

- [ ] **Step 1: Rewrite webhook/reports_nav.py to async**

Replace the full contents of `webhook/reports_nav.py`:

```python
"""Reports navigation helpers for the Telegram bot.

Provides /reports command UI: type selection -> latest / year -> month -> list.
Also provides handle_report_download() for the report_dl callback.

Supabase calls are sync — wrapped with asyncio.to_thread().
"""

import asyncio
import logging
import os

import aiohttp

from bot.config import get_bot
from bot.callback_data import (
    ReportType as ReportTypeCB, ReportYear, ReportMonth,
    ReportDownload, ReportBack, ReportYears,
)

logger = logging.getLogger(__name__)

# ── Supabase client (sync, own instance) ──

_supabase_client = None


def _get_supabase():
    global _supabase_client
    if _supabase_client is None:
        sb_url = os.environ.get("SUPABASE_URL")
        sb_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        if not sb_url or not sb_key:
            logger.warning("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set")
            return None
        from supabase import create_client
        _supabase_client = create_client(sb_url, sb_key)
    return _supabase_client


PT_MONTHS = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
    5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}

_esc = lambda s: str(s).replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`")


async def reports_show_types(chat_id, message_id=None):
    """Show report type selection."""
    from bot.keyboards import build_report_types_keyboard
    bot = get_bot()
    text = "📊 *Platts Reports*\n\nEscolha a categoria:"
    markup = build_report_types_keyboard()
    if message_id:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=markup)
    else:
        await bot.send_message(chat_id, text, reply_markup=markup)


def _query_latest_sync(report_type):
    sb = _get_supabase()
    if not sb:
        return None
    return sb.table("platts_reports") \
        .select("id, report_name, date_key, frequency") \
        .eq("report_type", report_type) \
        .order("date_key", desc=True) \
        .limit(10) \
        .execute()


async def reports_show_latest(chat_id, message_id, report_type):
    """Show the 10 most recent reports of a given type."""
    bot = get_bot()
    try:
        result = await asyncio.to_thread(_query_latest_sync, report_type)
    except Exception as exc:
        logger.error(f"reports latest query error: {exc}")
        await bot.edit_message_text("⚠️ Erro ao consultar relatórios", chat_id=chat_id, message_id=message_id)
        return

    if result is None:
        await bot.edit_message_text("⚠️ Supabase não configurado", chat_id=chat_id, message_id=message_id)
        return

    rows = result.data or []
    if not rows:
        keyboard = {"inline_keyboard": [[{"text": "⬅ Voltar", "callback_data": ReportBack(target="types").pack()}]]}
        await bot.edit_message_text("Nenhum relatório encontrado.", chat_id=chat_id, message_id=message_id, reply_markup=keyboard)
        return

    text = f"📊 *{_esc(report_type)}*\n\nÚltimos relatórios:"
    keyboard = []
    for r in rows:
        dk = r["date_key"]
        label = f"{_esc(r['report_name'])} — {dk}"
        keyboard.append([{"text": label, "callback_data": ReportDownload(report_id=str(r["id"])).pack()}])
    keyboard.append([
        {"text": "📅 Ver por data", "callback_data": ReportYears(report_type=report_type).pack()},
        {"text": "⬅ Voltar", "callback_data": ReportBack(target="types").pack()},
    ])
    await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup={"inline_keyboard": keyboard})


def _query_years_sync(report_type):
    sb = _get_supabase()
    if not sb:
        return None
    return sb.table("platts_reports").select("date_key").eq("report_type", report_type).execute()


async def reports_show_years(chat_id, message_id, report_type):
    """Show available years for a report type."""
    bot = get_bot()
    try:
        result = await asyncio.to_thread(_query_years_sync, report_type)
    except Exception as exc:
        logger.error(f"reports years query error: {exc}")
        await bot.edit_message_text("⚠️ Erro ao consultar anos", chat_id=chat_id, message_id=message_id)
        return

    if result is None:
        await bot.edit_message_text("⚠️ Supabase não configurado", chat_id=chat_id, message_id=message_id)
        return

    years = sorted({int(r["date_key"][:4]) for r in (result.data or [])}, reverse=True)
    text = f"📊 *{_esc(report_type)}*\n\nEscolha o ano:"
    keyboard = [[{"text": str(y), "callback_data": ReportYear(report_type=report_type, year=y).pack()}] for y in years]
    keyboard.append([{"text": "⬅ Voltar", "callback_data": ReportTypeCB(report_type=report_type).pack()}])
    await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup={"inline_keyboard": keyboard})


def _query_months_sync(report_type, year):
    sb = _get_supabase()
    if not sb:
        return None
    return sb.table("platts_reports") \
        .select("date_key") \
        .eq("report_type", report_type) \
        .gte("date_key", f"{year}-01-01") \
        .lte("date_key", f"{year}-12-31") \
        .execute()


async def reports_show_months(chat_id, message_id, report_type, year):
    """Show available months for a report type + year."""
    bot = get_bot()
    try:
        result = await asyncio.to_thread(_query_months_sync, report_type, year)
    except Exception as exc:
        logger.error(f"reports months query error: {exc}")
        await bot.edit_message_text("⚠️ Erro ao consultar meses", chat_id=chat_id, message_id=message_id)
        return

    if result is None:
        await bot.edit_message_text("⚠️ Supabase não configurado", chat_id=chat_id, message_id=message_id)
        return

    month_counts = {}
    for r in (result.data or []):
        m = int(r["date_key"][5:7])
        month_counts[m] = month_counts.get(m, 0) + 1
    months_sorted = sorted(month_counts.items(), reverse=True)

    text = f"📊 *{_esc(report_type)} — {year}*\n\nEscolha o mês:"
    keyboard = []
    for m, cnt in months_sorted:
        label = f"{PT_MONTHS.get(m, str(m))} ({cnt})"
        keyboard.append([{"text": label, "callback_data": ReportMonth(report_type=report_type, year=year, month=m).pack()}])
    keyboard.append([{"text": "⬅ Voltar", "callback_data": ReportYears(report_type=report_type).pack()}])
    await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup={"inline_keyboard": keyboard})


def _query_month_list_sync(report_type, year, month):
    sb = _get_supabase()
    if not sb:
        return None
    start = f"{year}-{month:02d}-01"
    end = f"{year + 1}-01-01" if month == 12 else f"{year}-{month + 1:02d}-01"
    return sb.table("platts_reports") \
        .select("id, report_name, date_key") \
        .eq("report_type", report_type) \
        .gte("date_key", start) \
        .lt("date_key", end) \
        .order("date_key", desc=True) \
        .order("report_name") \
        .execute()


async def reports_show_month_list(chat_id, message_id, report_type, year, month):
    """Show all reports for a given type + year + month."""
    bot = get_bot()
    try:
        result = await asyncio.to_thread(_query_month_list_sync, report_type, year, month)
    except Exception as exc:
        logger.error(f"reports month list query error: {exc}")
        await bot.edit_message_text("⚠️ Erro ao consultar relatórios do mês", chat_id=chat_id, message_id=message_id)
        return

    if result is None:
        await bot.edit_message_text("⚠️ Supabase não configurado", chat_id=chat_id, message_id=message_id)
        return

    rows = result.data or []
    month_name = PT_MONTHS.get(month, str(month))
    text = f"📊 *{_esc(report_type)} — {month_name} {year}*"
    if not rows:
        text += "\n\nNenhum relatório nesse período."

    keyboard = []
    for r in rows:
        day = r["date_key"][8:10]
        label = f"{_esc(r['report_name'])} — {day}/{month:02d}"
        keyboard.append([{"text": label, "callback_data": ReportDownload(report_id=str(r["id"])).pack()}])
    keyboard.append([{"text": "⬅ Voltar", "callback_data": ReportYear(report_type=report_type, year=year).pack()}])
    await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup={"inline_keyboard": keyboard})


def _download_report_sync(report_id):
    """Sync: query Supabase for report metadata + signed URL + PDF bytes."""
    import requests
    sb = _get_supabase()
    if not sb:
        return None, "Supabase não configurado"
    row = sb.table("platts_reports").select("storage_path, report_name").eq("id", report_id).single().execute()
    if not row.data:
        return None, "Relatório não encontrado"
    storage_path = row.data["storage_path"]
    report_name = row.data["report_name"]
    signed = sb.storage.from_("platts-reports").create_signed_url(storage_path, 3600)
    if not signed or not signed.get("signedURL"):
        return None, "Erro ao gerar link"
    pdf_url = signed["signedURL"]
    pdf_resp = requests.get(pdf_url, timeout=30)
    pdf_resp.raise_for_status()
    filename = storage_path.split("/")[-1]
    return {"content": pdf_resp.content, "filename": filename, "report_name": report_name}, None


async def handle_report_download(chat_id, callback_id, report_id):
    """Download a PDF report from Supabase and send as Telegram document.

    Returns (ok: bool, message: str).
    """
    try:
        result, error = await asyncio.to_thread(_download_report_sync, report_id)
    except Exception as exc:
        logger.error(f"report_dl error: {exc}")
        return False, "Erro ao baixar relatório"

    if result is None:
        return False, error

    bot = get_bot()
    from aiogram.types import BufferedInputFile
    doc = BufferedInputFile(result["content"], filename=result["filename"])
    await bot.send_document(chat_id, doc, caption=f"📄 {result['report_name']}")
    return True, result["report_name"]
```

- [ ] **Step 2: Commit**

```bash
git add webhook/reports_nav.py
git commit -m "feat(reports_nav): convert to async with asyncio.to_thread Supabase"
```

---

## Task 10: Command Handlers Router

**Files:**
- Create: `webhook/bot/routers/__init__.py`
- Create: `webhook/bot/routers/commands.py`

- [ ] **Step 1: Create webhook/bot/routers/__init__.py**

```python
"""Bot router package."""
```

- [ ] **Step 2: Create webhook/bot/routers/commands.py**

```python
"""All slash-command handlers.

Public router (no auth middleware): /start
Admin router (with AdminAuthMiddleware): everything else
"""

import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from bot.config import get_bot, ANTHROPIC_API_KEY, SHEET_ID, TELEGRAM_WEBHOOK_URL
from bot.states import AddContact, NewsInput
from bot.keyboards import build_main_menu_keyboard
from bot.middlewares.auth import AdminAuthMiddleware
import contact_admin
import query_handlers
from status_builder import build_status_message
from reports_nav import reports_show_types
from execution.integrations.sheets_client import SheetsClient

logger = logging.getLogger(__name__)

# ── Public router (no auth) ──

public_router = Router(name="commands_public")


@public_router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 *Minerals Trading Bot*\n\n"
        "*Notícias:*\n"
        "Cole texto — viro relatório via IA e envio pra aprovação.\n\n"
        "*Contatos (admin):*\n"
        "`/status` — status dos workflows\n"
        "`/add` — adicionar contato\n"
        "`/list [busca]` — listar e ativar/desativar\n"
        "`/cancel` — desistir do /add em curso",
    )


# ── Admin router (with auth middleware) ──

admin_router = Router(name="commands_admin")
admin_router.message.middleware(AdminAuthMiddleware())


@admin_router.message(Command("status"))
async def cmd_status(message: Message):
    try:
        body = build_status_message()
    except Exception as exc:
        logger.error(f"/status failed: {exc}")
        body = f"⚠️ Erro ao gerar status: {str(exc)[:100]}"
    await message.answer(body)


@admin_router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Cancelado.")


@admin_router.message(Command("add"))
async def cmd_add(message: Message, state: FSMContext):
    await state.set_state(AddContact.waiting_data)
    await message.answer(contact_admin.render_add_prompt())


@admin_router.message(Command("list"))
async def cmd_list(message: Message):
    parts = (message.text or "").split(None, 1)
    search = parts[1].strip() if len(parts) > 1 else None
    await _render_list_view(message.chat.id, page=1, search=search)


@admin_router.message(Command("reprocess"))
async def cmd_reprocess(message: Message):
    parts = (message.text or "").split(None, 1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            "Uso: `/reprocess <item_id>`\n\n"
            "O item\\_id é o `🆔` mostrado no rodapé dos cards de curadoria.\n"
            "Busca em staging (48h) e depois em archive (7d).",
        )
        return
    await _reprocess_item(message.chat.id, parts[1].strip())


@admin_router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(query_handlers.format_help())


@admin_router.message(Command("history"))
async def cmd_history(message: Message):
    try:
        body = query_handlers.format_history()
    except Exception as exc:
        logger.error(f"/history error: {exc}")
        await message.answer("❌ Erro ao consultar arquivo.")
        return
    await message.answer(body)


@admin_router.message(Command("stats"))
async def cmd_stats(message: Message):
    today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        body = query_handlers.format_stats(today_iso)
    except Exception as exc:
        logger.error(f"/stats error: {exc}")
        await message.answer("❌ Erro ao calcular stats.")
        return
    await message.answer(body)


@admin_router.message(Command("rejections"))
async def cmd_rejections(message: Message):
    try:
        body = query_handlers.format_rejections()
    except Exception as exc:
        logger.error(f"/rejections error: {exc}")
        await message.answer("❌ Erro ao listar recusas.")
        return
    await message.answer(body)


@admin_router.message(Command("queue"))
async def cmd_queue(message: Message):
    try:
        body, markup = query_handlers.format_queue_page(page=1)
    except Exception as exc:
        logger.error(f"/queue error: {exc}")
        await message.answer("❌ Erro ao consultar staging.")
        return
    await message.answer(body, reply_markup=markup)


@admin_router.message(Command("reports"))
async def cmd_reports(message: Message):
    await reports_show_types(message.chat.id)


@admin_router.message(Command("workflows"))
async def cmd_workflows(message: Message):
    from workflow_trigger import render_workflow_list
    wf_text, wf_markup = await render_workflow_list()
    await message.answer(wf_text, reply_markup=wf_markup)


@admin_router.message(Command("s"))
async def cmd_menu(message: Message):
    await message.answer("🥸 *SuperMustache BOT*", reply_markup=build_main_menu_keyboard())


# ── Helpers ──

async def _render_list_view(chat_id, page, search, message_id=None):
    """Fetch contacts and render list message with keyboard."""
    bot = get_bot()
    try:
        sheets = SheetsClient()
        per_page = 10
        contacts, total_pages = await asyncio.to_thread(
            sheets.list_contacts, SHEET_ID, search=search, page=page, per_page=per_page,
        )
        all_contacts, _ = await asyncio.to_thread(
            sheets.list_contacts, SHEET_ID, search=search, page=1, per_page=10_000,
        )
        total = len(all_contacts)

        msg = contact_admin.render_list_message(
            contacts, total=total, page=page, per_page=per_page, search=search,
        )
        kb = contact_admin.build_list_keyboard(
            contacts, page=page, total_pages=total_pages, search=search,
        )

        if message_id is None:
            await bot.send_message(chat_id, msg, reply_markup=kb)
        else:
            await bot.edit_message_text(msg, chat_id=chat_id, message_id=message_id, reply_markup=kb)
    except Exception as e:
        logger.error(f"_render_list_view failed: {e}")
        err_msg = "❌ Erro ao acessar planilha. Tente novamente."
        if message_id:
            await bot.edit_message_text(err_msg, chat_id=chat_id, message_id=message_id)
        else:
            await bot.send_message(chat_id, err_msg)


async def _reprocess_item(chat_id, item_id):
    """Re-run the 3-agent pipeline on a curation item pulled from Redis."""
    from bot.routers._helpers import find_curation_item, run_pipeline_and_archive
    bot = get_bot()
    item = await asyncio.to_thread(find_curation_item, item_id)
    if item is None:
        await bot.send_message(chat_id, f"❌ Item `{item_id}` não encontrado em staging nem archive recente.")
        return
    raw_text = (
        f"Title: {item.get('title', '')}\n"
        f"Date: {item.get('publishDate', '')}\n"
        f"Source: {item.get('source', '')}\n\n"
        f"{item.get('fullText', '')}"
    )
    progress = await bot.send_message(chat_id, f"🖋️ *Reprocessando via Writer*\n🆔 `{item_id}`")
    asyncio.create_task(run_pipeline_and_archive(chat_id, raw_text, progress.message_id, item_id))
```

- [ ] **Step 3: Commit**

```bash
git add webhook/bot/routers/__init__.py webhook/bot/routers/commands.py
git commit -m "feat(bot): add command handlers (public + admin routers)"
```

---

## Task 11: Shared Router Helpers

**Files:**
- Create: `webhook/bot/routers/_helpers.py`

- [ ] **Step 1: Create webhook/bot/routers/_helpers.py**

```python
"""Shared helpers used by multiple routers.

Extracted to avoid circular imports between commands.py and callbacks.py.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone, timedelta

from bot.config import get_bot
from bot.keyboards import build_approval_keyboard

logger = logging.getLogger(__name__)

# ── Persistent drafts store (Redis, 7d TTL) ──

_DRAFT_KEY_PREFIX = "webhook:draft:"
_DRAFT_TTL_SECONDS = 7 * 24 * 60 * 60


def _drafts_client():
    from execution.curation.redis_client import _get_client
    return _get_client()


def drafts_get(draft_id):
    try:
        raw = _drafts_client().get(f"{_DRAFT_KEY_PREFIX}{draft_id}")
        if raw:
            return json.loads(raw)
    except Exception as exc:
        logger.warning(f"drafts_get({draft_id}) failed: {exc}")
    return None


def drafts_set(draft_id, draft):
    try:
        _drafts_client().set(
            f"{_DRAFT_KEY_PREFIX}{draft_id}",
            json.dumps(draft),
            ex=_DRAFT_TTL_SECONDS,
        )
    except Exception as exc:
        logger.error(f"drafts_set({draft_id}) failed: {exc}")


def drafts_contains(draft_id):
    try:
        return bool(_drafts_client().exists(f"{_DRAFT_KEY_PREFIX}{draft_id}"))
    except Exception as exc:
        logger.warning(f"drafts_contains({draft_id}) failed: {exc}")
        return False


def drafts_update(draft_id, **fields):
    draft = drafts_get(draft_id)
    if draft is None:
        return
    draft.update(fields)
    drafts_set(draft_id, draft)


def find_curation_item(item_id):
    """Look up a Platts curation item by id in staging -> today/yesterday archive."""
    from execution.curation import redis_client
    try:
        item = redis_client.get_staging(item_id)
    except Exception as exc:
        logger.warning(f"reprocess staging lookup failed for {item_id}: {exc}")
        item = None
    if item is not None:
        return item
    now_utc = datetime.now(timezone.utc)
    for offset in (0, 1):
        date = (now_utc - timedelta(days=offset)).strftime("%Y-%m-%d")
        try:
            item = redis_client.get_archive(date, item_id)
        except Exception as exc:
            logger.warning(f"reprocess archive lookup failed ({date}, {item_id}): {exc}")
            continue
        if item is not None:
            return item
    return None


async def process_news(chat_id, raw_text, progress_msg_id):
    """Process news text through 3 agents as background task."""
    from execution.core.agents_progress import format_pipeline_progress
    from pipeline import run_3_agents

    bot = get_bot()
    phase_order = ["Writer", "Reviewer", "Finalizer"]
    done = []

    async def hook(phase_name):
        idx = phase_order.index(phase_name)
        done.clear()
        done.extend(phase_order[:idx])
        if progress_msg_id:
            await bot.edit_message_text(
                format_pipeline_progress(current=phase_name, done=list(done)),
                chat_id=chat_id,
                message_id=progress_msg_id,
            )

    try:
        if progress_msg_id:
            await bot.edit_message_text(
                format_pipeline_progress(current="Writer", done=[]),
                chat_id=chat_id,
                message_id=progress_msg_id,
            )

        final_message = await run_3_agents(raw_text, on_phase_start=hook)

        draft_id = f"news_{int(time.time())}"
        drafts_set(draft_id, {
            "message": final_message,
            "status": "pending",
            "original_text": raw_text,
            "uazapi_token": None,
            "uazapi_url": None,
        })

        if progress_msg_id:
            await bot.edit_message_text(
                format_pipeline_progress(current=None, done=list(phase_order)),
                chat_id=chat_id,
                message_id=progress_msg_id,
            )

        display = final_message[:3500] if len(final_message) > 3500 else final_message
        await bot.send_message(
            chat_id,
            f"📋 *PREVIEW*\n\n{display}",
            reply_markup=build_approval_keyboard(draft_id),
        )

    except Exception as e:
        logger.error(f"process_news failed: {e}")
        if progress_msg_id:
            remaining = [p for p in phase_order if p not in done]
            current = remaining[0] if remaining else None
            await bot.edit_message_text(
                format_pipeline_progress(current=current, done=list(done), error=str(e)[:120]),
                chat_id=chat_id,
                message_id=progress_msg_id,
            )


async def process_adjustment(chat_id, draft_id, feedback):
    """Adjust draft with user feedback as background task."""
    from pipeline import run_adjuster
    bot = get_bot()
    progress = await bot.send_message(chat_id, "⏳ Ajustando mensagem...")
    progress_msg_id = progress.message_id

    try:
        draft = drafts_get(draft_id)
        if not draft:
            await bot.send_message(chat_id, "❌ Draft não encontrado.")
            return

        adjusted = await run_adjuster(draft["message"], feedback, draft["original_text"])

        draft["message"] = adjusted
        draft["status"] = "pending"
        drafts_set(draft_id, draft)

        await bot.edit_message_text("✅ Ajuste concluído!", chat_id=chat_id, message_id=progress_msg_id)

        display = adjusted[:3500] if len(adjusted) > 3500 else adjusted
        await bot.send_message(
            chat_id,
            f"📋 *PREVIEW*\n\n{display}",
            reply_markup=build_approval_keyboard(draft_id),
        )
        logger.info(f"Draft {draft_id} adjusted")
    except Exception as e:
        logger.error(f"Adjustment error: {e}")
        await bot.edit_message_text(f"❌ Erro no ajuste:\n{str(e)[:500]}", chat_id=chat_id, message_id=progress_msg_id)


async def run_pipeline_and_archive(chat_id, raw_text, progress_msg_id, item_id):
    """Run pipeline then archive staging item on success."""
    from execution.curation import redis_client
    try:
        await process_news(chat_id, raw_text, progress_msg_id)
    except Exception as exc:
        logger.error(f"pipeline failed for {item_id}: {exc}")
        return
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        await asyncio.to_thread(redis_client.archive, item_id, date, chat_id=chat_id)
    except Exception as exc:
        logger.warning(f"archive post-success failed for {item_id}: {exc}")
```

- [ ] **Step 2: Commit**

```bash
git add webhook/bot/routers/_helpers.py
git commit -m "feat(bot): add shared router helpers (drafts, pipeline, archive)"
```

---

## Task 12: Callback Handlers Router

**Files:**
- Create: `webhook/bot/routers/callbacks.py`

- [ ] **Step 1: Create webhook/bot/routers/callbacks.py**

```python
"""All callback query handlers.

Replaces callback_router.py with Aiogram CallbackData-filtered handlers.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone

from aiogram import Router
from aiogram.types import CallbackQuery

from bot.config import get_bot, SHEET_ID
from bot.callback_data import (
    CurateAction, DraftAction, MenuAction,
    ReportType, ReportYear, ReportMonth, ReportDownload, ReportBack, ReportYears,
    QueuePage, QueueOpen,
    ContactToggle, ContactPage,
    WorkflowRun, WorkflowList,
)
from bot.keyboards import build_main_menu_keyboard
from bot.middlewares.auth import AdminAuthMiddleware
from bot.routers._helpers import (
    drafts_get, drafts_contains, drafts_update, drafts_set,
    process_news, process_adjustment, run_pipeline_and_archive,
)
import contact_admin
import query_handlers
import redis_queries
from status_builder import build_status_message
from reports_nav import (
    reports_show_types, reports_show_latest, reports_show_years,
    reports_show_months, reports_show_month_list, handle_report_download,
)
from execution.integrations.sheets_client import SheetsClient

logger = logging.getLogger(__name__)

callback_router = Router(name="callbacks")
callback_router.callback_query.middleware(AdminAuthMiddleware())


# ── Draft actions ──

@callback_router.callback_query(DraftAction.filter())
async def on_draft_action(query: CallbackQuery, callback_data: DraftAction):
    bot = get_bot()
    chat_id = query.message.chat.id
    action = callback_data.action
    draft_id = callback_data.draft_id

    if action == "approve":
        draft = drafts_get(draft_id)
        if not draft:
            await query.answer("❌ Draft não encontrado")
            await _finalize_card(query, "❌ *DRAFT EXPIRADO*\n\nRode o workflow novamente.")
            return
        if draft["status"] != "pending":
            await query.answer("⚠️ Já processado")
            await _finalize_card(query, f"⚠️ *Já processado* ({draft['status']})")
            return
        drafts_update(draft_id, status="approved")
        await query.answer("✅ Aprovado! Enviando...")
        await _finalize_card(
            query,
            f"✅ *Aprovado* em {datetime.now(timezone.utc).strftime('%H:%M')} UTC — envio em andamento",
        )
        from dispatch import process_approval_async
        asyncio.create_task(
            process_approval_async(chat_id, draft["message"], draft.get("uazapi_token"), draft.get("uazapi_url"))
        )

    elif action == "test_approve":
        draft = drafts_get(draft_id)
        if not draft:
            await query.answer("❌ Draft não encontrado")
            await _finalize_card(query, "❌ *Draft não encontrado*")
            return
        await query.answer("🧪 Enviando teste para 1 contato...")
        await _finalize_card(
            query,
            f"🧪 *Teste em andamento* — {datetime.now(timezone.utc).strftime('%H:%M')} UTC",
        )
        from dispatch import process_test_send_async
        asyncio.create_task(
            process_test_send_async(chat_id, draft_id, draft["message"], draft.get("uazapi_token"), draft.get("uazapi_url"))
        )

    elif action == "adjust":
        draft = drafts_get(draft_id)
        if not draft:
            await query.answer("❌ Draft não encontrado")
            await _finalize_card(query, "❌ *Draft não encontrado*")
            return
        from aiogram.fsm.context import FSMContext
        from bot.states import AdjustDraft
        state: FSMContext = query.bot.get("fsm_context")  # injected by dispatcher
        # Use the FSM from the callback query's user
        from aiogram.fsm.storage.base import StorageKey
        key = StorageKey(bot_id=bot.id, chat_id=chat_id, user_id=query.from_user.id)
        fsm = FSMContext(storage=bot.get("storage"), key=key)
        # Simpler: use state from data dict
        # Actually, Aiogram injects state into handler data automatically when using Router
        # We need to accept state as parameter
        pass  # handled in on_draft_adjust below

    elif action == "reject":
        snapshot_title = ""
        draft = drafts_get(draft_id)
        if draft:
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
            redis_queries.save_feedback(
                action="draft_reject", item_id=draft_id, chat_id=chat_id, reason="", title=snapshot_title,
            )
        except Exception as exc:
            logger.error(f"draft reject save_feedback error: {exc}")
        from bot.states import RejectReason
        from aiogram.fsm.context import FSMContext
        # Set FSM state for reject reason collection
        await query.answer("❌ Rejeitado")
        await _finalize_card(
            query,
            f"❌ *Recusado*\n🕒 {datetime.now(timezone.utc).strftime('%H:%M')} UTC\n\n"
            f"💭 Por quê? (opcional — responda ou `pular`)",
        )


# Separate handler for adjust action that accepts FSM state
@callback_router.callback_query(DraftAction.filter(action="adjust"))
async def on_draft_adjust(query: CallbackQuery, callback_data: DraftAction, state):
    from bot.states import AdjustDraft
    draft = drafts_get(callback_data.draft_id)
    if not draft:
        await query.answer("❌ Draft não encontrado")
        await _finalize_card(query, "❌ *Draft não encontrado*")
        return
    await state.set_state(AdjustDraft.waiting_feedback)
    await state.update_data(draft_id=callback_data.draft_id)
    await query.answer("✏️ Modo ajuste")
    await _finalize_card(query, "✏️ *Em modo ajuste* — envie o feedback na próxima mensagem")
    await query.message.answer(
        "✏️ *MODO AJUSTE*\n\n"
        "Envie uma mensagem descrevendo o que quer ajustar.\n\n"
        "Exemplos:\n"
        "• _Remova o terceiro parágrafo_\n"
        "• _Adicione que o preço subiu 2%_\n"
        "• _Resuma em menos linhas_\n"
        "• _Mude o título para X_",
    )


# ── Menu actions ──

@callback_router.callback_query(MenuAction.filter())
async def on_menu_action(query: CallbackQuery, callback_data: MenuAction):
    chat_id = query.message.chat.id
    await query.answer("")
    target = callback_data.target

    if target == "reports":
        await reports_show_types(chat_id)
    elif target == "queue":
        try:
            body, markup = query_handlers.format_queue_page(page=1)
        except Exception:
            return
        await query.message.answer(body, reply_markup=markup)
    elif target == "history":
        try:
            await query.message.answer(query_handlers.format_history())
        except Exception:
            pass
    elif target == "rejections":
        try:
            await query.message.answer(query_handlers.format_rejections())
        except Exception:
            pass
    elif target == "stats":
        try:
            today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            await query.message.answer(query_handlers.format_stats(today_iso))
        except Exception:
            pass
    elif target == "status":
        try:
            await query.message.answer(build_status_message())
        except Exception:
            pass
    elif target == "reprocess":
        await query.message.answer("Uso: `/reprocess <item\\_id>`\n\nDigite o comando com o ID do item.")
    elif target == "list":
        await query.message.answer("Uso: `/list [busca]`\n\nDigite o comando ou `/list` pra ver todos.")
    elif target == "add":
        await query.message.answer("Uso: `/add`\n\nDigite o comando pra iniciar.")
    elif target == "help":
        try:
            await query.message.answer(query_handlers.format_help())
        except Exception:
            pass


# ── Report navigation ──

@callback_router.callback_query(ReportType.filter())
async def on_report_type(query: CallbackQuery, callback_data: ReportType):
    await query.answer("")
    await reports_show_latest(query.message.chat.id, query.message.message_id, callback_data.report_type)


@callback_router.callback_query(ReportYears.filter())
async def on_report_years(query: CallbackQuery, callback_data: ReportYears):
    await query.answer("")
    await reports_show_years(query.message.chat.id, query.message.message_id, callback_data.report_type)


@callback_router.callback_query(ReportYear.filter())
async def on_report_year(query: CallbackQuery, callback_data: ReportYear):
    await query.answer("")
    await reports_show_months(query.message.chat.id, query.message.message_id, callback_data.report_type, callback_data.year)


@callback_router.callback_query(ReportMonth.filter())
async def on_report_month(query: CallbackQuery, callback_data: ReportMonth):
    await query.answer("")
    await reports_show_month_list(
        query.message.chat.id, query.message.message_id,
        callback_data.report_type, callback_data.year, callback_data.month,
    )


@callback_router.callback_query(ReportDownload.filter())
async def on_report_download(query: CallbackQuery, callback_data: ReportDownload):
    ok, msg = await handle_report_download(query.message.chat.id, query.id, callback_data.report_id)
    await query.answer(f"📤 {msg}" if ok else msg)


@callback_router.callback_query(ReportBack.filter())
async def on_report_back(query: CallbackQuery, callback_data: ReportBack):
    await query.answer("")
    chat_id = query.message.chat.id
    message_id = query.message.message_id
    target = callback_data.target
    if target == "types":
        await reports_show_types(chat_id, message_id=message_id)
    elif target.startswith("type:"):
        report_type = target[len("type:"):]
        await reports_show_latest(chat_id, message_id, report_type)
    elif target.startswith("years:"):
        report_type = target[len("years:"):]
        await reports_show_years(chat_id, message_id, report_type)
    elif target.startswith("year:"):
        parts = target[len("year:"):].rsplit(":", 1)
        if len(parts) == 2:
            await reports_show_months(chat_id, message_id, parts[0], int(parts[1]))


# ── Queue navigation ──

@callback_router.callback_query(QueuePage.filter())
async def on_queue_page(query: CallbackQuery, callback_data: QueuePage):
    await query.answer("")
    try:
        body, markup = query_handlers.format_queue_page(page=callback_data.page)
    except Exception as exc:
        logger.error(f"queue_page error: {exc}")
        return
    bot = get_bot()
    await bot.edit_message_text(
        body, chat_id=query.message.chat.id,
        message_id=query.message.message_id, reply_markup=markup,
    )


@callback_router.callback_query(QueueOpen.filter())
async def on_queue_open(query: CallbackQuery, callback_data: QueueOpen):
    from execution.curation import redis_client as curation_redis
    from execution.curation import telegram_poster
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
        # telegram_poster.post_for_curation is sync and uses telegram.py
        # Wrap in to_thread for now; will be fully adapted later
        await asyncio.to_thread(telegram_poster.post_for_curation, chat_id, item, preview_base_url)
    except Exception as exc:
        logger.error(f"queue_open post error: {exc}")
        await query.message.answer("❌ Erro ao abrir card.")


# ── Contact admin ──

@callback_router.callback_query(ContactToggle.filter())
async def on_contact_toggle(query: CallbackQuery, callback_data: ContactToggle):
    from bot.routers.commands import _render_list_view
    try:
        sheets = SheetsClient()
        name, new_status = await asyncio.to_thread(sheets.toggle_contact, SHEET_ID, callback_data.phone)
    except ValueError as e:
        await query.answer(f"❌ {str(e)[:100]}")
        return
    except Exception as e:
        logger.error(f"toggle_contact failed: {e}")
        await query.answer("❌ Erro")
        return

    toast = f"✅ {name} ativado" if new_status == "Big" else f"❌ {name} desativado"
    await query.answer(toast)
    await _render_list_view(query.message.chat.id, page=1, search=None, message_id=query.message.message_id)


@callback_router.callback_query(ContactPage.filter())
async def on_contact_page(query: CallbackQuery, callback_data: ContactPage):
    from bot.routers.commands import _render_list_view
    await query.answer("")
    search = callback_data.search if callback_data.search else None
    await _render_list_view(
        query.message.chat.id, page=callback_data.page,
        search=search, message_id=query.message.message_id,
    )


# ── Workflow actions ──

@callback_router.callback_query(WorkflowRun.filter())
async def on_workflow_run(query: CallbackQuery, callback_data: WorkflowRun):
    from workflow_trigger import trigger_workflow, find_triggered_run, poll_and_update, _workflow_name_by_id
    from bot.callback_data import WorkflowList as WfListCB
    chat_id = query.message.chat.id
    message_id = query.message.message_id
    workflow_id = callback_data.workflow_id
    name = _workflow_name_by_id(workflow_id)

    await query.answer(f"Disparando {name}...")
    bot = get_bot()
    await bot.edit_message_text(
        f"🚀 *Disparando {name}...*",
        chat_id=chat_id, message_id=message_id,
        reply_markup={"inline_keyboard": [[{"text": "⬅ Cancelar", "callback_data": WfListCB(action="list").pack()}]]},
    )

    ok, error = await trigger_workflow(workflow_id)
    if not ok:
        await bot.edit_message_text(
            f"❌ *{name}* — erro ao disparar\n\n`{error}`",
            chat_id=chat_id, message_id=message_id,
            reply_markup={"inline_keyboard": [
                [{"text": "🔄 Tentar novamente", "callback_data": WorkflowRun(workflow_id=workflow_id).pack()}],
                [{"text": "⬅ Workflows", "callback_data": WfListCB(action="list").pack()}],
            ]},
        )
        return

    await bot.edit_message_text(
        f"🔄 *{name}* rodando...\n\nAguardando conclusao.",
        chat_id=chat_id, message_id=message_id,
    )

    async def _track():
        run_id = await find_triggered_run(workflow_id)
        if run_id is None:
            await bot.edit_message_text(
                f"⚠️ *{name}* — disparado mas nao encontrei o run\n\nVerifique no GitHub.",
                chat_id=chat_id, message_id=message_id,
                reply_markup={"inline_keyboard": [[{"text": "⬅ Workflows", "callback_data": WfListCB(action="list").pack()}]]},
            )
            return
        await poll_and_update(chat_id, message_id, workflow_id, run_id)

    asyncio.create_task(_track())


@callback_router.callback_query(WorkflowList.filter())
async def on_workflow_list(query: CallbackQuery, callback_data: WorkflowList):
    await query.answer("")
    bot = get_bot()

    if callback_data.action == "list":
        from workflow_trigger import render_workflow_list
        text, markup = await render_workflow_list()
        await bot.edit_message_text(
            text, chat_id=query.message.chat.id,
            message_id=query.message.message_id, reply_markup=markup,
        )
    elif callback_data.action == "back_menu":
        await query.message.answer("🥸 *SuperMustache BOT*", reply_markup=build_main_menu_keyboard())


# ── Curation actions ──

@callback_router.callback_query(CurateAction.filter())
async def on_curate_action(query: CallbackQuery, callback_data: CurateAction):
    chat_id = query.message.chat.id
    item_id = callback_data.item_id
    action = callback_data.action

    if action == "archive":
        from execution.curation import redis_client
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            archived = await asyncio.to_thread(redis_client.archive, item_id, date, chat_id=chat_id)
        except Exception as exc:
            logger.error(f"curate_archive redis error: {exc}")
            await query.answer("⚠️ Redis indisponível, tenta de novo")
            return
        if archived is None:
            await query.answer("⚠️ Item expirou ou já processado")
            await _finalize_card(query, "⚠️ Item expirou ou já processado")
            return
        await query.answer("✅ Arquivado")
        await _finalize_card(
            query,
            f"✅ *Arquivado*\n🕒 {datetime.now(timezone.utc).strftime('%H:%M')} UTC · 🆔 `{item_id}`",
        )

    elif action == "reject":
        from execution.curation import redis_client
        snapshot_title = ""
        try:
            item = redis_client.get_staging(item_id)
            if item:
                snapshot_title = item.get("title") or ""
        except Exception:
            pass
        try:
            await asyncio.to_thread(redis_client.discard, item_id)
        except Exception as exc:
            logger.error(f"curate_reject redis error: {exc}")
            await query.answer("⚠️ Redis indisponível")
            return
        try:
            redis_queries.save_feedback(
                action="curate_reject", item_id=item_id, chat_id=chat_id, reason="", title=snapshot_title,
            )
        except Exception as exc:
            logger.error(f"curate_reject save_feedback error: {exc}")
        await query.answer("❌ Recusado")
        await _finalize_card(
            query,
            f"❌ *Recusado*\n🕒 {datetime.now(timezone.utc).strftime('%H:%M')} UTC · 🆔 `{item_id}`\n\n"
            f"💭 Por quê? (opcional — responda ou `pular`)",
        )

    elif action == "pipeline":
        from execution.curation import redis_client
        try:
            item = await asyncio.to_thread(redis_client.get_staging, item_id)
        except Exception as exc:
            logger.error(f"curate_pipeline redis error: {exc}")
            await query.answer("⚠️ Redis indisponível")
            return
        if item is None:
            await query.answer("⚠️ Item expirou")
            await _finalize_card(query, "⚠️ Item expirou ou já processado")
            return
        try:
            redis_queries.mark_pipeline_processed(item_id, datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        except Exception as exc:
            logger.warning(f"mark_pipeline_processed failed for {item_id}: {exc}")
        raw_text = (
            f"Title: {item.get('title', '')}\n"
            f"Date: {item.get('publishDate', '')}\n"
            f"Source: {item.get('source', '')}\n\n"
            f"{item.get('fullText', '')}"
        )
        await query.answer("🖋️ Enviando para o Writer...")
        bot = get_bot()
        progress = await bot.send_message(chat_id, f"🖋️ *Enviando para o Writer*\n🆔 `{item_id}`")
        await _finalize_card(
            query,
            f"🖋️ *Enviado para o Writer*\n🕒 {datetime.now(timezone.utc).strftime('%H:%M')} UTC · 🆔 `{item_id}`",
        )
        asyncio.create_task(run_pipeline_and_archive(chat_id, raw_text, progress.message_id, item_id))


# ── Nop callback ──

@callback_router.callback_query(lambda q: q.data in ("nop", "noop"))
async def on_nop(query: CallbackQuery):
    await query.answer("")


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
```

- [ ] **Step 2: Commit**

```bash
git add webhook/bot/routers/callbacks.py
git commit -m "feat(bot): add callback query handlers with CallbackData factories"
```

---

## Task 13: FSM Message Handlers Router

**Files:**
- Create: `webhook/bot/routers/messages.py`

- [ ] **Step 1: Create webhook/bot/routers/messages.py**

```python
"""FSM text input handlers.

Handles text messages when the user is in a specific FSM state
(adjust feedback, reject reason, add contact, free-form news text).
"""

import asyncio
import logging

from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from bot.config import get_bot, ANTHROPIC_API_KEY, SHEET_ID
from bot.states import AdjustDraft, RejectReason, AddContact, NewsInput
from bot.middlewares.auth import AdminAuthMiddleware
from bot.routers._helpers import process_news, process_adjustment
import contact_admin
import redis_queries
from execution.integrations.sheets_client import SheetsClient

logger = logging.getLogger(__name__)

message_router = Router(name="messages")
message_router.message.middleware(AdminAuthMiddleware())


@message_router.message(AdjustDraft.waiting_feedback, F.text)
async def on_adjust_feedback(message: Message, state: FSMContext):
    data = await state.get_data()
    draft_id = data.get("draft_id")
    await state.clear()
    if not draft_id:
        await message.answer("❌ Nenhum draft em ajuste.")
        return
    logger.info(f"Received adjustment feedback for {draft_id}")
    asyncio.create_task(process_adjustment(message.chat.id, draft_id, message.text))


@message_router.message(RejectReason.waiting_reason, F.text)
async def on_reject_reason(message: Message, state: FSMContext):
    data = await state.get_data()
    feedback_key = data.get("feedback_key")
    await state.clear()

    stripped = (message.text or "").strip()
    if stripped.lower() in {"pular", "skip"}:
        await message.answer("✅ Ok, sem razão registrada.")
        return

    if feedback_key:
        try:
            redis_queries.update_feedback_reason(feedback_key, stripped)
        except Exception as exc:
            logger.error(f"update_feedback_reason error: {exc}")
    await message.answer("✅ Razão registrada.")


@message_router.message(AddContact.waiting_data, F.text)
async def on_add_contact_data(message: Message, state: FSMContext):
    chat_id = message.chat.id
    text = message.text or ""

    # /cancel while in add flow
    if text.strip().startswith("/"):
        await state.clear()
        return

    try:
        name, phone = contact_admin.parse_add_input(text)
    except ValueError as e:
        await message.answer(f"❌ {e}")
        return  # keep state so user can retry

    try:
        sheets = SheetsClient()
        await asyncio.to_thread(sheets.add_contact, SHEET_ID, name, phone)
    except ValueError as e:
        await message.answer(f"❌ {e}")
        await state.clear()
        return
    except Exception as e:
        logger.error(f"add_contact failed: {e}")
        await message.answer("❌ Erro ao gravar na planilha. Tente novamente.")
        await state.clear()
        return

    try:
        sheets = SheetsClient()
        all_contacts, _ = await asyncio.to_thread(
            sheets.list_contacts, SHEET_ID, page=1, per_page=10_000,
        )
        active = sum(1 for c in all_contacts if str(c.get("ButtonPayload", "")).strip() == "Big")
    except Exception:
        active = "?"

    await message.answer(f"✅ {name} adicionado\nTotal ativos: {active}")
    await state.clear()


# ── Free-form news text (no FSM state — catch-all for text) ──

@message_router.message(F.text)
async def on_news_text(message: Message, state: FSMContext):
    """Process free-form text through the 3-agent pipeline."""
    if not ANTHROPIC_API_KEY:
        await message.answer("❌ ANTHROPIC\\_API\\_KEY não configurada no servidor.")
        return

    chat_id = message.chat.id
    text = message.text or ""
    logger.info(f"New news text from chat {chat_id} ({len(text)} chars)")

    progress = await message.answer("⏳ Processando sua notícia com 3 agentes IA...")
    asyncio.create_task(process_news(chat_id, text, progress.message_id))
```

- [ ] **Step 2: Commit**

```bash
git add webhook/bot/routers/messages.py
git commit -m "feat(bot): add FSM message handlers (adjust, reject, add-contact, news)"
```

---

## Task 14: aiohttp API Routes

**Files:**
- Create: `webhook/routes/__init__.py`
- Create: `webhook/routes/api.py`
- Create: `webhook/routes/preview.py`

- [ ] **Step 1: Create webhook/routes/__init__.py**

```python
"""aiohttp routes package for non-Telegram endpoints."""
```

- [ ] **Step 2: Create webhook/routes/api.py**

```python
"""aiohttp routes for GitHub Actions endpoints and admin operations.

These are plain HTTP routes — not Telegram handlers. They serve:
- POST /store-draft (GitHub Actions → store a draft for approval)
- GET/POST /seen-articles (GitHub Actions → dedup for market_news)
- GET /health (monitoring)
- GET /test-ai (Anthropic API connectivity test)
- POST /admin/register-commands (register bot commands with Telegram)
"""

import json
import logging
import os
from datetime import datetime, timedelta

import aiohttp
from aiohttp import web

from bot.config import ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN
from bot.routers._helpers import drafts_set
import contact_admin

logger = logging.getLogger(__name__)

# In-memory state for seen articles (ephemeral, not worth Redis for 3d TTL)
SEEN_ARTICLES: dict[str, set] = {}

routes = web.RouteTableDef()


@routes.get("/health")
async def health(request: web.Request) -> web.Response:
    from bot.config import UAZAPI_TOKEN, UAZAPI_URL
    return web.json_response({
        "status": "ok",
        "seen_articles_dates": len(SEEN_ARTICLES),
        "uazapi_token_set": bool(UAZAPI_TOKEN),
        "uazapi_url": UAZAPI_URL,
        "anthropic_key_set": bool(ANTHROPIC_API_KEY),
        "anthropic_key_prefix": ANTHROPIC_API_KEY[:10] + "..." if ANTHROPIC_API_KEY else "NONE",
    })


@routes.get("/test-ai")
async def test_ai(request: web.Request) -> web.Response:
    if not ANTHROPIC_API_KEY:
        return web.json_response({"error": "ANTHROPIC_API_KEY not set"}, status=500)
    try:
        from pipeline import call_claude
        result = await call_claude("You are helpful.", "Say 'hello' in one word.")
        return web.json_response({"status": "ok", "response": result[:100]})
    except Exception as e:
        return web.json_response(
            {"status": "error", "error_type": type(e).__name__, "error": str(e)[:500]},
            status=500,
        )


@routes.post("/store-draft")
async def store_draft(request: web.Request) -> web.Response:
    data = await request.json()
    draft_id = data.get("draft_id")
    message = data.get("message")
    if not draft_id or not message:
        return web.json_response({"error": "Missing draft_id or message"}, status=400)

    draft = {
        "message": message,
        "status": "pending",
        "original_text": "",
        "uazapi_token": (data.get("uazapi_token") or "").strip() or None,
        "uazapi_url": (data.get("uazapi_url") or "").strip() or None,
    }
    drafts_set(draft_id, draft)

    if draft["uazapi_token"]:
        logger.info(f"Draft includes UAZAPI token: {draft['uazapi_token'][:8]}...")
    else:
        logger.info(f"Draft has no UAZAPI token, will use env var")

    logger.info(f"Draft stored: {draft_id} ({len(message)} chars)")
    return web.json_response({"success": True, "draft_id": draft_id})


@routes.get("/seen-articles")
async def get_seen_articles(request: web.Request) -> web.Response:
    date = request.query.get("date", "")
    if not date:
        return web.json_response({"error": "Missing 'date' query parameter"}, status=400)
    titles = list(SEEN_ARTICLES.get(date, set()))
    return web.json_response({"date": date, "titles": titles})


@routes.post("/seen-articles")
async def store_seen_articles(request: web.Request) -> web.Response:
    data = await request.json()
    date = data.get("date", "")
    titles = data.get("titles", [])
    if not date or not titles:
        return web.json_response({"error": "Missing 'date' or 'titles'"}, status=400)

    if date not in SEEN_ARTICLES:
        SEEN_ARTICLES[date] = set()
    SEEN_ARTICLES[date].update(titles)

    # Prune entries older than 3 days
    try:
        cutoff = datetime.now() - timedelta(days=3)
        stale_keys = [k for k in SEEN_ARTICLES if datetime.strptime(k, "%Y-%m-%d") < cutoff]
        for k in stale_keys:
            del SEEN_ARTICLES[k]
    except ValueError as e:
        logger.warning(f"Date format mismatch during seen-articles pruning: {e}")

    logger.info(f"Stored {len(titles)} seen articles for {date} (total: {len(SEEN_ARTICLES.get(date, []))})")
    return web.json_response({"success": True, "stored": len(titles)})


@routes.post("/admin/register-commands")
async def register_commands(request: web.Request) -> web.Response:
    raw_chat_id = request.query.get("chat_id", "")
    try:
        chat_id = int(raw_chat_id)
    except ValueError:
        return web.json_response({"ok": False, "error": "chat_id query param required"}, status=400)
    if not contact_admin.is_authorized(chat_id):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=403)

    if not TELEGRAM_BOT_TOKEN:
        return web.json_response({"ok": False, "error": "TELEGRAM_BOT_TOKEN missing"}, status=500)

    commands = [
        {"command": "s", "description": "Menu principal com todos os atalhos"},
        {"command": "workflows", "description": "Disparar workflows (GitHub Actions)"},
        {"command": "reports", "description": "Consultar e baixar relatórios Platts (PDF)"},
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
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setMyCommands",
                json={"commands": commands},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
    except Exception as exc:
        logger.error(f"setMyCommands request failed: {exc}")
        return web.json_response({"ok": False, "error": str(exc)}, status=502)
    if not data.get("ok"):
        logger.error(f"setMyCommands returned not-ok: {data}")
        return web.json_response({"ok": False, "telegram": data}, status=502)
    logger.info(f"setMyCommands registered {len(commands)} commands")
    return web.json_response({"ok": True, "registered": len(commands)})
```

- [ ] **Step 3: Create webhook/routes/preview.py**

```python
"""aiohttp route for /preview/{item_id} with Jinja2 template rendering."""

import logging
from datetime import datetime, timedelta, timezone

import aiohttp_jinja2
from aiohttp import web

logger = logging.getLogger(__name__)

routes = web.RouteTableDef()


@routes.get("/preview/{item_id}")
async def preview_item(request: web.Request) -> web.Response:
    """Render Platts item HTML preview for Telegram in-app browser."""
    from execution.curation import redis_client

    item_id = request.match_info["item_id"]
    item = None

    try:
        item = redis_client.get_staging(item_id)
    except Exception as exc:
        logger.warning(f"Preview staging lookup failed: {exc}")

    if item is None:
        now_utc = datetime.now(timezone.utc)
        for offset in (0, 1):
            date = (now_utc - timedelta(days=offset)).strftime("%Y-%m-%d")
            try:
                item = redis_client.get_archive(date, item_id)
            except Exception as exc:
                logger.warning(f"Preview archive lookup failed ({date}): {exc}")
                continue
            if item is not None:
                break

    if item is None:
        return web.Response(
            text=(
                "<!DOCTYPE html><html lang='pt-BR'><head><meta charset='UTF-8'>"
                "<title>Item não encontrado</title></head><body>"
                "<h1>Item não encontrado</h1>"
                "<p>Expirou (48h) ou já foi processado.</p>"
                "</body></html>"
            ),
            content_type="text/html",
            status=404,
        )

    safe_item = dict(item)
    if not isinstance(safe_item.get("fullText"), str):
        safe_item["fullText"] = ""
    if not isinstance(safe_item.get("tables"), list):
        safe_item["tables"] = []

    return aiohttp_jinja2.render_template("preview.html", request, {"item": safe_item})
```

- [ ] **Step 4: Commit**

```bash
git add webhook/routes/__init__.py webhook/routes/api.py webhook/routes/preview.py
git commit -m "feat(routes): add aiohttp routes for store-draft, seen-articles, health, preview"
```

---

## Task 15: Main Entry Point

**Files:**
- Create: `webhook/bot/main.py`

- [ ] **Step 1: Create webhook/bot/main.py**

```python
"""Entry point: aiohttp app with Aiogram webhook handler.

Run: python -m webhook.bot.main
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

import aiohttp_jinja2
import jinja2
from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# Ensure webhook/ is on sys.path (same as Dockerfile COPY layout)
_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))
# Also add repo root for execution.* imports in local dev
sys.path.insert(0, str(_HERE.parent))

from bot.config import (
    get_bot, get_dispatcher,
    WEBAPP_HOST, WEBAPP_PORT, WEBHOOK_PATH, TELEGRAM_WEBHOOK_URL,
    TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY, UAZAPI_URL, UAZAPI_TOKEN,
)
from bot.routers.commands import public_router, admin_router
from bot.routers.callbacks import callback_router
from bot.routers.messages import message_router
from routes.api import routes as api_routes
from routes.preview import routes as preview_routes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Background task tracking ──
_background_tasks: set[asyncio.Task] = set()


def create_background_task(coro):
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


async def on_startup(app: web.Application):
    bot = get_bot()
    webhook_url = f"{TELEGRAM_WEBHOOK_URL}{WEBHOOK_PATH}"
    await bot.set_webhook(webhook_url)
    logger.info(f"Webhook set to {webhook_url}")

    # Log config
    logger.info(f"UAZAPI_URL: {UAZAPI_URL}")
    logger.info(f"UAZAPI_TOKEN: {'SET (' + UAZAPI_TOKEN[:8] + '...)' if UAZAPI_TOKEN else 'NOT SET'}")
    logger.info(f"TELEGRAM_BOT_TOKEN: {'SET' if TELEGRAM_BOT_TOKEN else 'NOT SET'}")
    logger.info(f"ANTHROPIC_API_KEY: {'SET' if ANTHROPIC_API_KEY else 'NOT SET'}")


async def on_shutdown(app: web.Application):
    bot = get_bot()
    await bot.delete_webhook()
    await bot.session.close()
    logger.info("Bot shut down cleanly")


def create_app() -> web.Application:
    # Dispatcher + routers
    dp = get_dispatcher()
    dp.include_router(public_router)
    dp.include_router(admin_router)
    dp.include_router(callback_router)
    dp.include_router(message_router)

    # aiohttp app
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    # Jinja2 for preview template
    templates_dir = str(_HERE / "templates")
    aiohttp_jinja2.setup(app, loader=jinja2.FileSystemLoader(templates_dir))

    # Mount aiohttp routes
    app.router.add_routes(api_routes)
    app.router.add_routes(preview_routes)

    # Mount Aiogram webhook handler
    bot = get_bot()
    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path=WEBHOOK_PATH)

    # Alternative: setup_application for lifecycle hooks
    # setup_application(app, dp, bot=bot)

    return app


def main():
    app = create_app()
    web.run_app(app, host=WEBAPP_HOST, port=WEBAPP_PORT)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add webhook/bot/main.py
git commit -m "feat(bot): add main entry point with aiohttp + Aiogram webhook"
```

---

## Task 16: Deployment Config

**Files:**
- Modify: `Dockerfile`
- Modify: `railway.json`

- [ ] **Step 1: Update Dockerfile**

Replace the full contents of `Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY webhook/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY webhook/ ./webhook/
COPY execution/ ./execution/
COPY .github/workflows/ ./.github/workflows/

ENV PORT=8080
EXPOSE 8080

CMD ["python", "-m", "webhook.bot.main"]
```

- [ ] **Step 2: Update railway.json**

Replace the full contents of `railway.json`:

```json
{
    "$schema": "https://railway.app/railway.schema.json",
    "build": {
        "builder": "DOCKERFILE",
        "dockerfilePath": "Dockerfile"
    },
    "deploy": {
        "restartPolicyType": "ON_FAILURE",
        "restartPolicyMaxRetries": 10,
        "startCommand": "python -m webhook.bot.main"
    }
}
```

- [ ] **Step 3: Commit**

```bash
git add Dockerfile railway.json
git commit -m "chore: update Dockerfile and railway.json for Aiogram entry point"
```

---

## Task 17: Delete Replaced Files

**Files:**
- Delete: `webhook/app.py`
- Delete: `webhook/telegram.py`
- Delete: `webhook/callback_router.py`

- [ ] **Step 1: Delete the replaced files**

```bash
git rm webhook/app.py webhook/telegram.py webhook/callback_router.py
```

- [ ] **Step 2: Commit**

```bash
git commit -m "refactor: remove Flask app.py, telegram.py, callback_router.py (replaced by Aiogram)"
```

---

## Task 18: Fix Existing Tests

**Files:**
- Modify: `tests/test_workflow_trigger.py`
- Modify: `tests/test_reject_reason_flow.py`
- Modify: `tests/test_webhook_status.py`
- Modify: `tests/test_digest.py`
- Modify: `tests/test_query_handlers.py`

The existing tests reference the old Flask modules. They need to be updated to work with the new async module structure. The key changes are:

1. `workflow_trigger.py` functions are now `async` — tests need `pytest-asyncio`
2. `test_reject_reason_flow.py` tested `app.py` functions — needs rewrite to test FSM flow
3. Other tests that only test pure logic (redis_queries, query_handlers, digest, etc.) should still pass unchanged since those modules weren't modified

- [ ] **Step 1: Verify which tests pass as-is**

Run: `pytest tests/ -v --ignore=tests/test_reject_reason_flow.py --ignore=tests/test_workflow_trigger.py -x`
Expected: Most tests should still pass. Note any failures.

- [ ] **Step 2: Update test_workflow_trigger.py for async**

The functions are now async, so tests need `pytest-asyncio`. Replace the full file:

```python
"""Tests for webhook/workflow_trigger.py (async version)."""
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

import pytest


@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_fake_token")
    monkeypatch.setenv("GITHUB_OWNER", "bigodinhc")
    monkeypatch.setenv("GITHUB_REPO", "workflows_minerals")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake:token")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")


@pytest.fixture
def wf():
    if "workflow_trigger" in sys.modules:
        del sys.modules["workflow_trigger"]
    import workflow_trigger
    return workflow_trigger


def test_catalog_has_5_workflows(wf):
    assert len(wf.WORKFLOW_CATALOG) == 5
    ids = [w["id"] for w in wf.WORKFLOW_CATALOG]
    assert "morning_check.yml" in ids
    assert "daily_report.yml" in ids


def test_catalog_entries_have_required_fields(wf):
    for w in wf.WORKFLOW_CATALOG:
        assert "id" in w
        assert "name" in w
        assert "description" in w


@pytest.mark.asyncio
@patch("workflow_trigger.aiohttp.ClientSession")
async def test_trigger_workflow_success(mock_session_cls, wf):
    mock_resp = AsyncMock()
    mock_resp.status = 204
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.post.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_session.post.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_session_cls.return_value = mock_session

    ok, error = await wf.trigger_workflow("morning_check.yml")
    assert ok is True
    assert error is None
```

- [ ] **Step 3: Run updated workflow_trigger tests**

Run: `pytest tests/test_workflow_trigger.py -v`
Expected: Pass

- [ ] **Step 4: Commit**

```bash
git add tests/test_workflow_trigger.py
git commit -m "test: update workflow_trigger tests for async"
```

---

## Task 19: Integration Smoke Test

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -v --tb=short`
Note all failures and fix incrementally.

- [ ] **Step 2: Fix any import path issues**

The main expected issue: modules that used to `from app import ...` or `from telegram import ...` need updating. Check each failure, fix the import, and re-run.

- [ ] **Step 3: Verify the app starts locally**

Set required env vars and try starting:

```bash
cd webhook
TELEGRAM_BOT_TOKEN=test REDIS_URL=redis://localhost:6379 ANTHROPIC_API_KEY=test python -c "
from bot.main import create_app
app = create_app()
print('App created successfully')
print('Routes:', [r.resource for r in app.router.routes()])
"
```

Expected: "App created successfully" with all routes listed.

- [ ] **Step 4: Commit any fixes**

```bash
git add -A
git commit -m "fix: resolve import paths and test compatibility for Aiogram migration"
```

---

## Task 20: Final Verification

- [ ] **Step 1: Run full test suite with coverage**

Run: `pytest tests/ -v --tb=short`
Expected: All tests pass. Note the count — should be >= 295 (original count).

- [ ] **Step 2: Verify no Flask imports remain in webhook/**

Run: `grep -r "from flask" webhook/ || echo "Clean"`
Expected: "Clean"

Run: `grep -r "import flask" webhook/ || echo "Clean"`
Expected: "Clean"

- [ ] **Step 3: Verify no threading.Thread remains in webhook/**

Run: `grep -r "threading.Thread" webhook/ || echo "Clean"`
Expected: "Clean"

- [ ] **Step 4: Verify no raw telegram.py imports remain**

Run: `grep -r "from telegram import" webhook/ || echo "Clean"`
Expected: "Clean" (all should use `from bot.config import get_bot` now)

- [ ] **Step 5: Verify Dockerfile builds**

Run: `docker build -t supermustache-bot .`
Expected: Successful build

- [ ] **Step 6: Commit and tag**

```bash
git add -A
git commit -m "feat: complete Flask → Aiogram 3 migration (Phase 1)"
git tag v2.0.0-aiogram
```
