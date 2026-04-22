# Coding Conventions

**Analysis Date:** 2026-04-22

## Naming Patterns

**Modules:**
- `snake_case` filenames: `callback_data.py`, `contacts_repo.py`, `delivery_reporter.py`
- Logical organization by domain: `routers/callbacks_*.py`, `core/delivery_reporter.py`, `integrations/contacts_repo.py`

**Classes:**
- `PascalCase`: `Contact`, `ContactsRepo`, `RoleMiddleware`, `ContactNotFoundError`, `WorkflowLogger`
- Exception classes follow domain: `ContactNotFoundError`, `InvalidPhoneError`, `ContactAlreadyExistsError`

**Functions:**
- `snake_case`: `list_active()`, `get_by_phone()`, `normalize_phone()`, `build_approval_keyboard()`
- Async handlers: `async def on_contact_toggle()`, `async def on_bulk_prompt()`
- Private functions: `_get_client()`, `_row_to_contact()`, `_parse_ts()`, `_user_key()`

**Variables:**
- `snake_case` for all: `draft_id`, `chat_id`, `message_id`, `phone_uazapi`, `total_pages`
- Constants in `UPPERCASE`: `ADMIN_CHAT_ID`, `DEFAULT_SUBSCRIPTIONS`, `TELEGRAM_BOT_TOKEN`

**Type Hints:**
- `Optional[T]` for nullable values: `Optional[str]`, `Optional[dict]`
- Return type hints on all functions: `def get_user(chat_id: int) -> Optional[dict]:`
- Union types with `|` operator (Python 3.10+): `Client | None`, `list | tuple`

## Code Style

**Formatting:**
- No explicit formatter config found (black, ruff, or autopep8 not in pyproject.toml)
- 4-space indentation (Python standard)
- Line length: no hard limit enforced; code ranges 80-100 chars in examples
- Imports at top of file with standard Python ordering

**Future Annotations:**
- All modules with type hints use `from __future__ import annotations` at top:
  ```python
  """Module docstring."""
  from __future__ import annotations
  
  import os
  import logging
  from typing import Optional
  ```

**Docstring Style:**
- Module-level docstrings describe purpose: `"""User store: Redis-backed CRUD for Telegram bot users."""`
- Class docstrings explain responsibility: `"""User pressed 'Rejeitar' — optionally sends a reason."""`
- Function docstrings include Args/Raises when non-obvious:
  ```python
  def add(
      self,
      name: str,
      phone_raw: str,
      *,
      send_welcome: Callable[[str], None],
  ) -> Contact:
      """Add a contact after validating the phone and dispatching a welcome.
      
      Flow: normalize → duplicate pre-check → send_welcome → insert.
      
      Raises:
        InvalidPhoneError: if the phone cannot be normalized.
        ContactAlreadyExistsError: if phone_uazapi already present.
        RuntimeError: if `send_welcome` raises.
      """
  ```

## Import Organization

**Order:**
1. `from __future__ import annotations` (always first)
2. Standard library: `os`, `logging`, `json`, `asyncio`
3. Third-party: `aiogram`, `phonenumbers`, `structlog`
4. Local relative: `from bot.config import ...`, `from execution.integrations import ...`

**Pattern - Local imports to avoid circular deps:**
```python
@callbacks_contacts_router.callback_query(ContactToggle.filter())
async def on_contact_toggle(query: CallbackQuery, callback_data: ContactToggle):
    # Local import avoids circular dep with commands.py.
    from bot.routers.commands import _render_list_view
```

**Path aliases:**
- Relative imports within package: `from bot.config import ...`
- Absolute imports across packages: `from execution.integrations.contacts_repo import Contact`
- sys.path manipulation in conftest.py to support test imports:
  ```python
  sys.path.insert(0, str(_REPO_ROOT / "webhook"))
  ```

## Data Models

**Dataclasses preferred over plain dicts:**
```python
@dataclass(frozen=True)
class Contact:
    id: str
    name: str
    phone_raw: str
    phone_uazapi: str
    status: str             # 'ativo' | 'inativo'
    created_at: datetime
    updated_at: datetime

    def is_active(self) -> bool:
        return self.status == "ativo"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat()
        d["updated_at"] = self.updated_at.isoformat()
        return d
```

**Frozen dataclasses for immutability:** `@dataclass(frozen=True)` prevents accidental mutation.

**CallbackData for type-safe button data:**
```python
class ContactToggle(CallbackData, prefix="tgl"):
    phone: str

class ContactBulk(CallbackData, prefix="bulk"):
    """First tap on bulk activate/deactivate."""
    status: str       # 'ativo' | 'inativo'
    search: str = ""
```
Replaces manual string parsing; auto-serializes to callback_data strings.

## Error Handling

**Custom Exception hierarchy:**
```python
class ContactNotFoundError(Exception):
    """No contact matches the given phone/id."""

class InvalidPhoneError(ValueError):
    """normalize_phone rejected the input."""

class ContactAlreadyExistsError(Exception):
    def __init__(self, existing: "Contact"):
        self.existing = existing
        super().__init__(f"Contact {existing.name!r} already exists")
```

**Try/except pattern with logging:**
```python
try:
    repo = ContactsRepo()
    contact = await asyncio.to_thread(repo.toggle, callback_data.phone)
except ContactNotFoundError as e:
    await query.answer(f"❌ {str(e)[:100]}")
    return
except Exception as e:
    logger.error(f"toggle_contact failed: {e}")
    await query.answer("❌ Erro")
    return
```

**Re-raising with context:**
```python
try:
    send_welcome(canonical)
except Exception as e:
    raise RuntimeError(f"welcome send failed: {e}") from e
```

## Logging

**Framework:** `logging` module (standard library)

**Pattern:**
```python
logger = logging.getLogger(__name__)
```

**Usage levels:**
- `logger.error()`: unrecoverable failures (human action needed)
- `logger.warning()`: soft failures, degraded mode, missing optional config
- `logger.info()`: state transitions, sentry_initialized
- `logger.debug()`: role authorization checks, trace-level detail

**Example:**
```python
logger.error(f"toggle_contact failed: {e}")
logger.warning(f"state_store: redis connection failed: {exc}")
logger.debug(f"Role '{role}' not in {self.allowed_roles} for chat_id={from_user.id}")
```

**Structured logging (WorkflowLogger):**
```python
from execution.core.logger import WorkflowLogger

logger = WorkflowLogger("my_workflow")
logger.info("Processing started", {"items": 10})
logger.error("Send failed", {"error": str(e), "contact": phone})
```
Writes JSON logs to `.tmp/logs/{workflow}/{run_id}.json` for post-run analysis.

## Async/Await Patterns

**Async handlers in routers:**
```python
@callbacks_contacts_router.callback_query(ContactToggle.filter())
async def on_contact_toggle(query: CallbackQuery, callback_data: ContactToggle):
    try:
        repo = ContactsRepo()
        contact = await asyncio.to_thread(repo.toggle, callback_data.phone)
    except Exception as e:
        logger.error(f"toggle_contact failed: {e}")
        await query.answer("❌ Erro")
```

**Running sync code in thread pool:**
```python
contact = await asyncio.to_thread(repo.toggle, callback_data.phone)
contact = await asyncio.to_thread(
    repo.list_all, search=search, page=1, per_page=10_000,
)
```
Avoids blocking the Telegram update loop on Supabase client operations.

**No `asyncio.create_task()` without supervision:**
Handlers use `await` to ensure completion before responding.

## Repository Pattern

**Dependency injection:**
```python
class ContactsRepo:
    def __init__(self, client=None):
        if client is not None:
            self.client = client
        else:
            from supabase import create_client
            url = os.environ.get("SUPABASE_URL")
            key = os.environ.get("SUPABASE_KEY")
            if not url or not key:
                raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set")
            self.client = create_client(url, key)
```
Constructor accepts optional `client` for testing; defaults to real Supabase client.

**Method cohesion:**
- Read methods: `list_active()`, `list_all()`, `get_by_phone()`
- Write methods: `add()`, `toggle()`, `bulk_set_status()`
- All I/O through self.client (chainable query builder)

## Middleware Pattern

**Type-safe role-based auth:**
```python
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
```

**Router registration:**
```python
callbacks_contacts_router = Router(name="callbacks_contacts")
callbacks_contacts_router.callback_query.middleware(
    RoleMiddleware(allowed_roles={"admin"})
)
```

## Configuration Management

**Environment-based singletons:**
```python
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
        _bot = Bot(token=TELEGRAM_BOT_TOKEN, ...)
    return _bot
```

**Eager env var loading at module level:**
```python
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
REDIS_URL = os.getenv("REDIS_URL", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL")  # Raises if missing at instantiation
```

## Comments

**When to comment:**
- **Non-obvious flows:** "Local import avoids circular dep with commands.py"
- **Complex business logic:** "Duplicate pre-check — avoid sending welcome to someone on the list"
- **Enum mapping:** PT labels for error categories
- Avoid restating what code obviously does: `x = 5  # set x to 5` is noise

**Comment style:**
```python
# ── Toggle ──           # Section headers with dashes
# Single-line explanation of intent or gotcha
```

---

*Convention analysis: 2026-04-22*
