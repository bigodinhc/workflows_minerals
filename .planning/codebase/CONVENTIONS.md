# Coding Conventions

**Analysis Date:** 2026-04-17

## Naming Patterns

**Files:**
- Python: `snake_case.py` - modules, scripts, test files (e.g., `dispatch.py`, `pipeline.py`, `test_mini_auth.py`)
- TypeScript/React: `camelCase.ts`, `camelCase.tsx`, `PascalCase.tsx` for components (e.g., `useApi.ts`, `GlassCard.tsx`)
- Tests: `test_*.py` (pytest convention) or `*.test.tsx`, `*.test.ts` (vitest convention in mini-app)

**Functions:**
- Python: `snake_case` - all function definitions follow snake_case strictly
- TypeScript: `camelCase` for functions, async functions, hooks
- React Components: `PascalCase` for exported components (e.g., `function GlassCard(...)`)

**Variables:**
- Python: `snake_case` - all variables and constants follow snake_case (e.g., `_STAGING_TTL_SECONDS`, `_client`, `fake_redis`)
- TypeScript: `camelCase` for variables, properties, parameters
- Python constants: `UPPER_SNAKE_CASE` for module-level constants (e.g., `_STAGING_TTL_SECONDS = 48 * 60 * 60`)

**Types:**
- Python: Type hints in function signatures using `typing` module - `Optional[T]`, `dict`, `list`, dataclasses with `@dataclass`
  - Example: `def get_staging(item_id: str) -> Optional[dict]:`
  - Dataclass usage: `@dataclass class Contact:` (in `execution/core/delivery_reporter.py`)
- TypeScript: Interface declarations for props and types (e.g., `interface GlassCardProps { children: React.ReactNode }`)
- TypeScript: Inline types with `type` keyword for exported utility types (e.g., `type Stats = { health_pct: number }`)

## Code Style

**Formatting:**
- Python: No automatic formatter configured (ruff/Black not detected in configs)
- TypeScript: TypeScript strict mode enabled via `tsconfig.json` (`strict: true`)
  - `noUnusedLocals: true`, `noUnusedParameters: true`, `noFallthroughCasesInSwitch: true`
  - No ESLint config detected; relies on TypeScript compiler checks
- Python convention: 4-space indentation (standard Python)
- TypeScript convention: 2-space indentation (Vite/modern JS default)

**Linting:**
- Python: No linter config detected (no `.pylintrc`, `.flake8`, `ruff.toml`)
- TypeScript: TypeScript compiler in strict mode acts as linter
- No ESLint/Prettier configs found; code relies on editor defaults

## Import Organization

**Order:**
1. Future imports (`from __future__ import annotations`) - always first in Python files
2. Standard library imports (`asyncio`, `json`, `logging`, `os`, `time`, `sys`)
3. Third-party imports (`aiohttp`, `aiogram`, `anthropic`, `pytest`, `redis`)
4. Local/relative imports (`from bot.config import ...`, `import contact_admin`)

**Examples:**

Python (`webhook/dispatch.py`):
```python
from __future__ import annotations

import asyncio
import json
import logging

import aiohttp
import requests

from bot.config import get_bot, UAZAPI_URL, UAZAPI_TOKEN, GOOGLE_CREDENTIALS_JSON, SHEET_ID
from bot.keyboards import build_approval_keyboard
from execution.core.delivery_reporter import DeliveryReporter, build_contact_from_row
from execution.integrations.sheets_client import SheetsClient
```

TypeScript (`webhook/mini-app/src/pages/Home.tsx`):
```typescript
import { useApi } from "../hooks/useApi";
import { GlassCard } from "../components/GlassCard";
import { RingChart } from "../components/RingChart";
import type { Stats, WorkflowsResponse, Workflow } from "../lib/types";
```

**Path Aliases:**
- Not used (no `paths` config in `tsconfig.json` or `baseUrl`)
- Relative imports common: `../hooks/`, `../components/`, `../lib/`

## Error Handling

**Patterns:**

Python:
- Try/except blocks wrapping async operations and API calls
- Specific exception types when possible (e.g., `anthropic.APIConnectionError`, `anthropic.AuthenticationError`)
- Fallback to broad `Exception` for external APIs with unpredictable failures
- Logging errors with `logger.error(f"message: {e}")` before re-raising

Example from `webhook/pipeline.py`:
```python
try:
    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY, timeout=120.0)
    message = await client.messages.create(...)
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
```

- For HTTP/external service errors: catch by status code or exception type, log details, return boolean or raise
- Telegram API Markdown escaping: Special handling in error messages to escape special characters

TypeScript/aiohttp:
- HTTP handlers raise specific exceptions (`web.HTTPUnauthorized`, `web.HTTPForbidden`)
- Promise-based error handling with async/await and try/catch

Example from `webhook/routes/mini_auth.py`:
```python
try:
    data = safe_parse_webapp_init_data(TELEGRAM_BOT_TOKEN, init_data)
except ValueError:
    raise web.HTTPUnauthorized(text="Invalid initData signature")
```

## Logging

**Framework:** Python `logging` module (built-in) — all modules use `logger = logging.getLogger(__name__)`

**Patterns:**
- Module-level logger: `logger = logging.getLogger(__name__)` (in `webhook/dispatch.py`, `webhook/pipeline.py`, `webhook/bot/config.py`)
- Log levels used: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`
- Info-level for workflow progress (e.g., `logger.info(f"Writer done ({len(writer_output)} chars)")`)
- Warning-level for retries/backoff (e.g., `logger.warning(f"Google Sheets API error {e}. Retrying in {sleep_time}s...")`)
- Error-level for exceptions and failures (e.g., `logger.error(f"Failed to fetch contacts after {max_retries} attempts: {e}")`)
- Structured logging via `WorkflowLogger` class in `execution/core/logger.py` for JSON logs with workflow/run context

Example from `execution/core/logger.py`:
```python
class WorkflowLogger:
    """Structured logger for workflow execution with JSON output."""
    def _log(self, level: str, message: str, data: Optional[dict] = None):
        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "workflow": self.workflow,
            "run_id": self.run_id,
            "step": self.step,
            "level": level,
            "message": message,
            "data": data or {}
        }
```

## Comments

**When to Comment:**
- Before complex functions or flows (e.g., "─── Google Sheets (contacts) ───" before `_get_contacts_sync()`)
- For non-obvious business logic (e.g., Telegram Markdown escaping requirements)
- For workarounds or known limitations

**Docstrings/Comments Style:**

Python:
- Triple-quoted docstrings for functions and classes
- Format: Single-line summary, blank line, optional description, Args/Returns sections
- Example from `execution/core/logger.py`:
  ```python
  def __init__(self, workflow: str, run_id: Optional[str] = None):
      """
      Initialize logger for a workflow.
      
      Args:
          workflow: Name of the workflow/directive
          run_id: Optional run ID (auto-generated if not provided)
      """
  ```

- Module-level docstrings with overview and usage examples (e.g., `webhook/dispatch.py` starts with module docstring)

TypeScript/React:
- JSDoc-style comments for components (e.g., `interface GlassCardProps { children: React.ReactNode; className?: string; }`)
- Inline comments for complex logic
- No strict DocString enforcement detected

## Function Design

**Size:** Typically 30–100 lines for async operations, 10–50 lines for pure functions
- Shorter functions preferred; async functions naturally longer due to await/error handling
- Examples: `validate_init_data()` (30 lines), `call_claude()` (25 lines), `send_whatsapp()` (20 lines)

**Parameters:**
- Python: Named parameters with type hints; optional params use defaults
- TypeScript: Props as single object parameter for React components (destructured in function signature)
- Example (TypeScript): `function GlassCard({ children, className = "" }: GlassCardProps)`

**Return Values:**
- Python async: Return the result directly, raise exceptions on failure
- TypeScript: Return typed objects (interfaces), use nullish values for missing data
- Example: `async def get_staging(item_id: str) -> Optional[dict]:` returns None if not found

## Module Design

**Exports:**

Python:
- Functions/classes exported implicitly (no `__all__` unless re-exporting from submodules)
- Example from `execution/core/prompts/__init__.py`:
  ```python
  from execution.core.prompts.writer import WRITER_SYSTEM
  from execution.core.prompts.critique import CRITIQUE_SYSTEM
  __all__ = ["WRITER_SYSTEM", "CRITIQUE_SYSTEM", "CURATOR_SYSTEM", "ADJUSTER_SYSTEM"]
  ```

TypeScript:
- Named exports for functions, components, interfaces (e.g., `export function useApi<T>(...)`)
- `export default` for page components (e.g., `export default function Home(...)`)

**Barrel Files:**
- Used in `execution/core/prompts/__init__.py` to aggregate agent system prompts
- Used in mini-app for potential component grouping (not extensive)

## Async Patterns

**Python (asyncio + aiohttp + Aiogram):**
- Async function definition: `async def function_name():`
- Await calls: `await call_claude(...)`, `await asyncio.to_thread(_get_contacts_sync())`, `await session.post(...)`
- Background task creation: `asyncio.create_task(coro)` with cleanup callback (in `webhook/bot/main.py`)
- Async context managers: `async with aiohttp.ClientSession() as session:`, `async with session.post(...) as resp:`

Example from `webhook/pipeline.py`:
```python
async def run_3_agents(raw_text: str, on_phase_start=None) -> str:
    async def _notify(phase_name):
        if on_phase_start is None:
            return
        result = on_phase_start(phase_name)
        if asyncio.iscoroutine(result):
            await result
    
    await _notify("Writer")
    writer_output = await call_claude(WRITER_SYSTEM, user_prompt)
```

**TypeScript (React + SWR for data fetching):**
- React hooks with side effects: `useEffect`, `useState`, `useSWR`
- Example from `webhook/mini-app/src/hooks/useApi.ts`:
  ```typescript
  export function useApi<T>(path: string | null, config?: SWRConfiguration<T>) {
    const { initData } = useTelegram();
    return useSWR<T>(
      path && initData ? path : null,
      (url: string) => apiFetch<T>(url, initData),
      { revalidateOnFocus: false, ...config }
    );
  }
  ```

## Type Hints

**Python:**
- Type hints used extensively in function signatures and variable declarations
- `from typing import Optional, Union, Callable, Iterable, Dict, List`
- Example: `def archive(item_id: str, date: str, chat_id: int) -> Optional[dict]:`
- Dataclass-based typing for structured data (e.g., `@dataclass class Contact:`)
- No MyPy configuration detected; type hints are informational/self-documenting

**TypeScript:**
- TypeScript strict mode enforces type checking at compile time (`strict: true` in `tsconfig.json`)
- Generics used for reusable components/hooks (e.g., `function useApi<T>(...)`)
- Interface-based prop typing for React components

## Configuration & Secrets

**Environment variables:**
- Read via `os.getenv(name, default)` in Python (e.g., `TELEGRAM_BOT_TOKEN`, `REDIS_URL`, `ANTHROPIC_API_KEY`)
- `.env` file exists and is gitignored; critical for local development
- Secrets passed to GitHub Actions via `${{ secrets.VARIABLE_NAME }}`
- Example from `webhook/bot/config.py`:
  ```python
  TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
  REDIS_URL = os.getenv("REDIS_URL", "")
  ANTHROPIC_API_KEY = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
  ```

**Secrets handling:**
- Never hardcoded in source; always from environment
- Partial logging of secrets (e.g., `UAZAPI_TOKEN[:8] + '...'`) for non-sensitive confirmation
- JSON secrets (e.g., `GOOGLE_CREDENTIALS_JSON`) loaded from env and parsed inline

## Commit Message Conventions

**Format:** `<type>: <description>`

Types observed:
- `feat:` - new feature (e.g., "feat(bot): broadcast message — send free-form text direct to WhatsApp")
- `fix:` - bug fix (e.g., "fix(broadcast): use StateFilter(None) on catch-all to prevent 3-agent activation")
- `refactor:` - code refactoring without behavior change (e.g., "refactor(bot): remove catch-all text handler")

**Style:**
- Lowercase type and description
- Parenthetical scope: `type(scope): description`
- Description uses em-dashes (—) to separate rationale
- Specific and actionable (e.g., "prevent 3-agent activation" not "fix bug")

## Code Quality Markers

**No mutations:**
- Python dataclasses used for immutable data (via `@dataclass`)
- Dictionaries copied before modification: `item = dict(item)` before setting fields

**Docstring requirement:**
- Module-level docstrings present in most workflow files
- Function docstrings for public/async APIs
- Not enforced uniformly across all utilities

**Line length:**
- Python: No strict limit detected; typical 80–120 characters
- TypeScript: No enforced limit; matches editor defaults (~100 chars)

---

*Convention analysis: 2026-04-17*
