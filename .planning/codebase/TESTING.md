# Testing Patterns

**Analysis Date:** 2026-04-22

## Test Framework

**Runner:**
- pytest 7.0.0+
- Config: `pytest.ini` at project root
- Python version: 3.9+

**Test discovery:**
```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = -v --tb=short
```

**Run Commands:**
```bash
pytest tests/                          # Run all tests
pytest tests/test_contacts_repo.py     # Run single module
pytest -k test_contact_toggle          # Run by name pattern
pytest --tb=short                      # Shorter traceback format
pytest -v                              # Verbose output
```

**Additional libraries:**
- `pytest-mock>=3.10.0` — for monkeypatching and fixtures
- `pytest-asyncio>=0.21,<1.0` — for `@pytest.mark.asyncio` async test support
- `fakeredis>=2.20,<3.0` — in-memory Redis for testing without Docker

## Test File Organization

**Location:** Co-located in `tests/` directory parallel to source

**Naming:** `test_*.py` files correspond to modules:
- `webhook/bot/users.py` → `tests/test_bot_users.py`
- `execution/integrations/contacts_repo.py` → `tests/test_contacts_repo.py`
- `execution/core/delivery_reporter.py` → `tests/test_delivery_reporter.py`

**File structure (55 test files total, 8870 lines):**
```
tests/
├── conftest.py                      # Shared fixtures (81 lines)
├── _manual_format_check.py          # Legacy format checker
├── test_bot_*.py                    # Bot handler & component tests
├── test_callbacks_*.py              # Router callback tests
├── test_contacts_*.py               # ContactsRepo and bulk ops
├── test_delivery_reporter.py        # Delivery tracking (936 lines)
├── test_event_bus.py                # Event system (625 lines)
├── test_state_store.py              # Workflow state persistence
├── test_progress_reporter.py        # Progress tracking (764 lines)
└── test_*.py                        # Unit/integration tests
```

**Imports path setup in conftest:**
```python
"""Shared pytest fixtures."""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
# Add repo root to sys.path so tests can import execution.* modules
sys.path.insert(0, str(_REPO_ROOT))
# Add webhook/ so bare imports (`import redis_queries`) resolve the same way
# they do in production (Dockerfile copies webhook/ contents to /app/).
sys.path.insert(0, str(_REPO_ROOT / "webhook"))
```

## Test Structure

**Fixture pattern (conftest.py):**
```python
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
```

**Fixture factories return callable:**
- `mock_callback_query()` accepts parameters to customize mock per test
- `mock_message()` returns fresh instance with customizable text, chat_id, user_id
- `fsm_context_in_state()` configures FSM state and data dict

## Mocking Strategy

**Mocking framework:** `unittest.mock` (built-in)
- `AsyncMock()` for async methods
- `MagicMock()` for sync methods with specs
- `patch()` for import-time injection

**Fake client builders for repo tests:**
```python
class FakeQuery:
    """Minimal chainable builder: mirrors supabase-py's PostgrestBuilder."""
    def __init__(self, data, count=None):
        self._data = data
        self._count = count
        self.calls = []

    def select(self, *a, **kw): self.calls.append(("select", a, kw)); return self
    def eq(self, *a, **kw):     self.calls.append(("eq", a, kw)); return self
    def ilike(self, *a, **kw):  self.calls.append(("ilike", a, kw)); return self
    def execute(self):
        r = MagicMock()
        r.data = self._data
        r.count = self._count
        return r


@pytest.fixture
def fake_client():
    client = MagicMock()
    client._queries = []
    return client


def _set_next_query(client, query: FakeQuery):
    """Configure client.table(...) to return `query` on the next call."""
    client.table.return_value = query
    client._queries.append(query)
```

**Repo usage in tests:**
```python
def test_list_active_filters_by_status_and_orders(fake_client):
    q = FakeQuery([_row(), _row(name="Bob")])
    _set_next_query(fake_client, q)

    repo = ContactsRepo(client=fake_client)
    contacts = repo.list_active()

    assert len(contacts) == 2
    assert contacts[0].name == "Alice"
    ops = [c[0] for c in q.calls]
    assert ops == ["select", "eq", "order"]
```

**Async handler mocking with patch:**
```python
@pytest.mark.asyncio
async def test_bulk_prompt_shows_confirmation(fake_query):
    """First tap on [❌ Desativar todos] must show confirmation keyboard."""
    fake_repo = MagicMock()
    fake_repo.list_all.return_value = ([MagicMock() for _ in range(47)], 5)

    with patch("webhook.bot.routers.callbacks_contacts.ContactsRepo",
               return_value=fake_repo):
        await on_bulk_prompt(
            fake_query,
            ContactBulk(status="inativo", search=""),
        )

    fake_query.message.edit_text.assert_called_once()
    call = fake_query.message.edit_text.call_args
    text = call.args[0] if call.args else call.kwargs.get("text")
    assert "47" in str(text)
```

**What to mock:**
- External services: Supabase, Redis, Telegram API, UazAPI
- Database calls (use fake client builders)
- Network I/O
- Long-running operations (asyncio.to_thread calls)

**What NOT to mock (test the real thing):**
- CallbackData serialization/deserialization: `ContactToggle.pack()` / `.unpack()`
- Business logic: phone normalization, status checks, filtering
- Data validation: `InvalidPhoneError` on bad input
- Dataclass behavior

## Fixtures and Factories

**Test data builders (inline):**
```python
def _row(**overrides):
    base = {
        "id": "00000000-0000-0000-0000-000000000001",
        "name": "Alice",
        "phone_raw": "+5511987654321",
        "phone_uazapi": "5511987654321",
        "status": "ativo",
        "created_at": "2026-04-22T10:00:00+00:00",
        "updated_at": "2026-04-22T10:00:00+00:00",
    }
    base.update(overrides)
    return base


def test_list_active_filters_by_status_and_orders(fake_client):
    q = FakeQuery([_row(), _row(name="Bob")])
    _set_next_query(fake_client, q)
    ...
```

**Welcome callback recorders:**
```python
class FakeWelcomeRecorder:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail
    def __call__(self, phone_uazapi: str):
        self.calls.append(phone_uazapi)
        if self.fail:
            raise RuntimeError("uazapi send failed")


def test_add_happy_path_sends_welcome_then_inserts(fake_client):
    dup_q = FakeQuery([])
    insert_q = FakeQuery([_row(name="Carol", phone_uazapi="5511900000001")])
    
    fake_client.table.side_effect = [dup_q, insert_q]
    
    welcome = FakeWelcomeRecorder()
    repo = ContactsRepo(client=fake_client)
    
    contact = repo.add("Carol", "+55 11 90000-0001", send_welcome=welcome)
    
    assert welcome.calls == ["5511900000001"]
```

## Test Types

### Unit Tests

**Scope:** Single function/method in isolation

**Example - Phone normalization:**
```python
def test_get_by_phone_normalizes_input(fake_client):
    q = FakeQuery([_row()])
    _set_next_query(fake_client, q)

    repo = ContactsRepo(client=fake_client)
    c = repo.get_by_phone("+55 (11) 98765-4321")

    assert c.phone_uazapi == "5511987654321"
    assert ("eq", ("phone_uazapi", "5511987654321"), {}) in q.calls
```

**Example - CallbackData pack/unpack:**
```python
def test_contact_toggle_pack_unpack():
    cb = ContactToggle(phone="5511999999999")
    packed = cb.pack()
    parsed = ContactToggle.unpack(packed)
    assert parsed.phone == "5511999999999"
```

### Integration Tests

**Scope:** Multiple layers (handler → repo → mock client)

**Example - Async callback handler:**
```python
@pytest.mark.asyncio
async def test_bulk_confirm_calls_bulk_set_status_and_reports(fake_query):
    fake_repo = MagicMock()
    fake_repo.bulk_set_status.return_value = 47

    with patch("webhook.bot.routers.callbacks_contacts.ContactsRepo",
               return_value=fake_repo), \
         patch("bot.routers.commands._render_list_view", new=AsyncMock()):
        await on_bulk_confirm(
            fake_query,
            ContactBulkConfirm(status="ativo", search=""),
        )

    fake_repo.bulk_set_status.assert_called_once()
    fake_query.answer.assert_called()
```

**Example - Error handling flow:**
```python
def test_add_welcome_failure_rolls_back_insert(fake_client):
    dup_q = FakeQuery([])
    _set_next_query(fake_client, dup_q)

    welcome = FakeWelcomeRecorder(fail=True)
    repo = ContactsRepo(client=fake_client)

    with pytest.raises(RuntimeError, match="welcome send failed"):
        repo.add("Carol", "+5511900000002", send_welcome=welcome)
    
    # insert_q should never have been called
    assert len(fake_client.table.call_args_list) == 1  # only dup check
```

## Async Testing

**Pattern with pytest-asyncio:**
```python
@pytest.mark.asyncio
async def test_contact_toggle_success(fake_query):
    fake_repo = MagicMock()
    fake_repo.toggle.return_value = Contact(...)
    
    with patch("webhook.bot.routers.callbacks_contacts.ContactsRepo",
               return_value=fake_repo):
        await on_contact_toggle(fake_query, ContactToggle(phone="5511999999999"))
    
    fake_query.answer.assert_called()
```

**Running sync methods in threads (mirror production):**
```python
@pytest.mark.asyncio
async def test_toggle_runs_sync_in_thread(fake_query):
    # Handler uses: contact = await asyncio.to_thread(repo.toggle, ...)
    # Test mocks the repo call, not asyncio.to_thread
    fake_repo = MagicMock()
    fake_repo.toggle.return_value = Contact(...)  # sync return
    
    with patch("webhook.bot.routers.callbacks_contacts.ContactsRepo",
               return_value=fake_repo):
        await on_contact_toggle(fake_query, ContactToggle(...))
```

## Error/Exception Testing

**Pattern - pytest.raises captures custom exceptions:**
```python
def test_get_by_phone_raises_when_missing(fake_client):
    q = FakeQuery([])
    _set_next_query(fake_client, q)

    repo = ContactsRepo(client=fake_client)
    with pytest.raises(ContactNotFoundError):
        repo.get_by_phone("+5511900000001")


def test_get_by_phone_invalid_input_raises_invalid_phone(fake_client):
    repo = ContactsRepo(client=fake_client)
    with pytest.raises(InvalidPhoneError):
        repo.get_by_phone("abc")


def test_add_duplicate_pre_check_raises_and_skips_send(fake_client):
    dup_q = FakeQuery([_row(name="Alice Existing")])
    _set_next_query(fake_client, dup_q)

    welcome = FakeWelcomeRecorder()
    repo = ContactsRepo(client=fake_client)

    with pytest.raises(ContactAlreadyExistsError) as exc_info:
        repo.add("Alice", "+5511987654321", send_welcome=welcome)

    assert exc_info.value.existing.name == "Alice Existing"
    assert welcome.calls == []  # Never called before exception
```

## Coverage

**Target:** No explicit enforcement in pytest.ini or pyproject.toml

**Observed coverage by category:**
- `execution/core/`: well-tested (delivery_reporter 936 lines, event_bus 625 lines)
- `execution/integrations/`: well-tested (contacts_repo 368 lines)
- `webhook/bot/routers/`: callback handlers tested (callbacks_contacts, callbacks_curation, etc.)
- Bot models (states, callback_data): 100% coverage via pack/unpack tests

**Coverage gaps:**
- No explicit coverage requirement → assume 60-70% baseline
- Error paths and edge cases prioritized over happy paths

## Test Independence

**FSM isolation:**
```python
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

**Message isolation:**
```python
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
```

Each test gets a fresh MagicMock instance (no state pollution).

## CI Integration

**No explicit GitHub Actions workflow found in repo**

**Local testing:**
```bash
pytest tests/                          # All tests
pytest tests/ -v                       # Verbose
pytest tests/ --tb=short               # Shorter tracebacks
```

**Expected pytest output:**
```
tests/test_contacts_repo.py::test_contact_is_active_true_for_ativo PASSED
tests/test_contacts_repo.py::test_list_active_filters_by_status_and_orders PASSED
...
```

---

*Testing analysis: 2026-04-22*
