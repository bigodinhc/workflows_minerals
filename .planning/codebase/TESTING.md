# Testing Patterns

**Analysis Date:** 2026-04-17

## Test Framework Overview

**Summary:** Mixed testing stack — pytest for Python (execution, webhook), vitest for TypeScript mini-app. Moderate test coverage with clear gaps in async/cloud-dependent workflows. **WARNING: Many core workflows lack test coverage (see Coverage Gaps section).**

## Python Testing

### Test Framework

**Runner:**
- pytest (version >=7.0.0 from `requirements.txt`)
- Config: `pytest.ini` at project root

**pytest.ini:**
```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = -v --tb=short
```

**Assertion Library:**
- Built-in `assert` statements (standard pytest)
- `pytest.raises()` for exception testing
- `pytest-mock` (version >=3.10.0) for mocking with `monkeypatch`, `mocker` fixtures

**Run Commands:**
```bash
pytest                    # Run all tests in tests/ directory
pytest tests/test_file.py # Run specific file
pytest -v               # Verbose output
pytest -k "test_name"   # Run tests matching name pattern
pytest --tb=short       # Short traceback (as configured)
```

### Test File Organization

**Location:**
- All Python tests in `/tests/` directory at project root (co-located with source)
- Example test files:
  - `tests/test_mini_auth.py` — Telegram webhook auth validation
  - `tests/test_redis_queries.py` — Redis keyspace operations
  - `tests/test_contact_admin.py` — Contact admin FSM logic
  - `tests/test_state_store.py` — Redis state storage
  - `tests/test_sheets_contact_ops.py` — Google Sheets integration (243 lines)

**Naming Convention:**
- `test_*.py` for test modules (pytest auto-discovery)
- 33 test files in total; approximately 5,000 lines of test code

**Directory Structure:**
```
tests/
├── conftest.py                          # Shared fixtures
├── test_mini_auth.py                    # Telegram auth validation
├── test_redis_queries.py                # Redis client operations
├── test_contact_admin.py                # Contact admin flows
├── test_state_store.py                  # FSM state persistence
├── test_sheets_contact_ops.py           # Google Sheets sync (243 lines)
├── test_curation_redis_client.py        # Platts curation staging
├── test_curation_telegram_poster.py     # Telegram message posting
├── test_agents_progress.py              # Agent run tracking
├── test_prompts.py                      # Agent prompt validation
└── ... (23 more test files)
```

### Test Structure

**Shared Fixtures** (`tests/conftest.py`):
```python
"""Shared pytest fixtures."""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "webhook"))
```
- Adds both repo root and webhook/ to `sys.path` so tests can import:
  - `execution.*` modules (from repo root)
  - `webhook` bare imports (for production parity with Docker layout)

**Per-Test Fixtures** (examples):

`tests/test_redis_queries.py`:
```python
@pytest.fixture
def fake_redis(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    from webhook import redis_queries
    monkeypatch.setattr(redis_queries, "_get_client", lambda: fake)
    return fake

@pytest.fixture(autouse=True)
def _reset_client_cache(monkeypatch):
    from webhook import redis_queries
    monkeypatch.setattr(redis_queries, "_client", None)
```
- Fixture injects `fakeredis.FakeRedis()` instance
- Auto-resets module-level `_client` cache between tests to prevent cross-test pollution

### Test Patterns

**Unit Test Anatomy:**

Simple test (from `test_redis_queries.py`):
```python
def test_list_staging_empty(fake_redis):
    from webhook.redis_queries import list_staging
    assert list_staging() == []

def test_list_staging_sorted_newest_first(fake_redis):
    from webhook.redis_queries import list_staging
    fake_redis.set("platts:staging:a", json.dumps({"id": "a", "title": "A", "stagedAt": "2026-04-15T10:00:00Z"}))
    fake_redis.set("platts:staging:b", json.dumps({"id": "b", "title": "B", "stagedAt": "2026-04-15T12:00:00Z"}))
    result = list_staging()
    assert [d["id"] for d in result] == ["b", "c", "a"]
```

**Async Test Pattern** (`tests/test_mini_auth.py`):
```python
@pytest.mark.asyncio
async def test_valid_init_data():
    from routes.mini_auth import validate_init_data
    init_data = _make_init_data()
    request = FakeRequest(headers={"X-Telegram-Init-Data": init_data})
    with patch("routes.mini_auth.TELEGRAM_BOT_TOKEN", TEST_TOKEN):
        with patch("routes.mini_auth.get_user_role", return_value="admin"):
            result = await validate_init_data(request)
            assert result.user is not None
            assert result.user.id == 12345
```

**Exception Testing** (`tests/test_mini_auth.py`):
```python
@pytest.mark.asyncio
async def test_missing_header_returns_401():
    from aiohttp.web import HTTPUnauthorized
    from routes.mini_auth import validate_init_data
    
    request = FakeRequest(headers={})
    with patch("routes.mini_auth.TELEGRAM_BOT_TOKEN", TEST_TOKEN):
        with pytest.raises(HTTPUnauthorized):
            await validate_init_data(request)
```

### Async Testing

**Framework:** `pytest-asyncio` (version >=0.21,<1.0 from `requirements.txt`)

**Pattern:**
- `@pytest.mark.asyncio` decorator on async test functions
- `await` used directly in test body
- Example: 8 async tests in `test_mini_auth.py` (lines 56–118)

### Mocking

**Framework:** `unittest.mock` (built-in) + `pytest-mock`

**Patterns:**

Redis mocking (fakeredis):
```python
@pytest.fixture
def fake_redis(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(redis_queries, "_get_client", lambda: fake)
    return fake
```

Environment/module patching:
```python
with patch("routes.mini_auth.TELEGRAM_BOT_TOKEN", TEST_TOKEN):
    with patch("routes.mini_auth.get_user_role", return_value="admin"):
        result = await validate_init_data(request)
```

**What to Mock:**
- External services (Redis via fakeredis, Google Sheets via return_value)
- Environment variables and module-level config
- API clients (Anthropic, Telegram) with mocked responses
- Filesystem operations (use temporary directories or monkeypatch)

**What NOT to Mock:**
- Database operations in integration tests (use fake Redis or in-memory stores)
- Data transformation logic (test actual behavior, not mocks)
- Function parameters and returns within same module

### Integration Tests

**Real Dependencies:**
- `fakeredis` library (>=2.20,<3.0) provides in-memory Redis for integration testing
- Example: `test_redis_queries.py` uses `FakeRedis(decode_responses=True)` to test Redis keyspace operations without network
- Google Sheets tests mock the API client but test actual data parsing/transformation

**Example** (`test_redis_queries.py`):
```python
def test_list_archive_recent_crossdate_sorted(fake_redis):
    """Test that items across multiple dates sort by timestamp (newest first)."""
    fake_redis.set("platts:archive:2026-04-13:x", json.dumps({"id": "x", "archivedAt": "2026-04-13T09:00:00+00:00"}))
    fake_redis.set("platts:archive:2026-04-15:y", json.dumps({"id": "y", "archivedAt": "2026-04-15T14:00:00+00:00"}))
    result = list_archive_recent(limit=10)
    assert [d["id"] for d in result] == ["y", "z", "x"]
```

### Fixture Patterns

**Factory Pattern** (from `test_mini_auth.py`):
```python
def _make_init_data(
    token: str = TEST_TOKEN,
    user_id: int = 12345,
    first_name: str = "Test",
    extra_params: dict | None = None,
) -> str:
    """Generate a correctly signed Telegram initData string."""
    user = json.dumps({"id": user_id, "first_name": first_name})
    params = {"user": user, "auth_date": str(int(time.time())), **(extra_params or {})}
    # ... signature generation ...
    return urlencode(params)
```

**Monkeypatch Fixtures** (from `test_redis_queries.py`):
```python
@pytest.fixture(autouse=True)
def _reset_client_cache(monkeypatch):
    """Auto-reset Redis client cache to prevent cross-test pollution."""
    from webhook import redis_queries
    monkeypatch.setattr(redis_queries, "_client", None)
```

---

## TypeScript Testing (Mini-App)

### Test Framework

**Runner:**
- vitest (version ^3.0.0 from `webhook/mini-app/package.json`)
- React Testing Library (version ^16.0.0)
- jsdom (version ^26.0.0) for DOM environment

**package.json Scripts:**
```json
{
  "scripts": {
    "test": "vitest run",
    "test:watch": "vitest"
  }
}
```

**No vitest.config.ts found** — using default vitest configuration

### Test File Organization

**Location:** Co-located with source files in `__tests__` subdirectories

**Directory Structure:**
```
webhook/mini-app/src/
├── components/
│   ├── __tests__/
│   │   ├── GlassCard.test.tsx
│   │   ├── TabBar.test.tsx
│   │   ├── Sparkline.test.tsx
│   │   ├── StatusDot.test.tsx
│   │   ├── Skeleton.test.tsx
│   │   ├── FilterChips.test.tsx
│   │   ├── MenuRow.test.tsx
│   │   └── RingChart.test.tsx
│   ├── GlassCard.tsx
│   ├── TabBar.tsx
│   └── ... (other components)
├── lib/
│   ├── __tests__/
│   │   └── format.test.ts
│   └── format.ts
└── pages/
    ├── __tests__/
    │   ├── News.test.tsx
    │   ├── Home.test.tsx
    │   └── Workflows.test.tsx
    ├── Home.tsx
    ├── News.tsx
    └── ... (other pages)
```

**Naming:** `*.test.ts`, `*.test.tsx` (vitest auto-discovery pattern)

**Test Count:** 12 test files in mini-app; approximately 50–100 lines of test code per page/component

### Test Structure

**Component Test Pattern** (`webhook/mini-app/src/components/__tests__/GlassCard.test.tsx`):
```typescript
import { render, screen } from "@testing-library/react";
import { GlassCard } from "../GlassCard";

test("renders children", () => {
  render(<GlassCard>Hello</GlassCard>);
  expect(screen.getByText("Hello")).toBeInTheDocument();
});

test("applies glass class", () => {
  const { container } = render(<GlassCard>Content</GlassCard>);
  expect(container.firstChild).toHaveClass("glass");
});

test("merges custom className", () => {
  const { container } = render(<GlassCard className="p-4">Content</GlassCard>);
  expect(container.firstChild).toHaveClass("p-4");
});
```

**Utility Test Pattern** (`webhook/mini-app/src/lib/__tests__/format.test.ts`):
```typescript
import { formatRelativeTime, formatDuration } from "../format";

describe("formatRelativeTime", () => {
  test("returns 'agora' for recent timestamps", () => {
    const now = new Date().toISOString();
    expect(formatRelativeTime(now)).toBe("agora");
  });

  test("returns minutes for < 1 hour", () => {
    const date = new Date(Date.now() - 15 * 60 * 1000).toISOString();
    expect(formatRelativeTime(date)).toBe("15min");
  });
});
```

### Assertion Library

**Framework:** Vitest built-in assertions (extends Jest API)
- `expect().toBeInTheDocument()` via `@testing-library/jest-dom`
- `expect().toBe()`, `expect().toEqual()`, `expect().toHaveClass()`, etc.
- `describe()` blocks for test grouping (shown in format.test.ts)

### Mocking

**React Testing Library mocking:**
- Component test isolation via `render()` without external deps
- Real component rendering (not shallow)

**Pattern (example):**
```typescript
// No explicit mocks shown in current tests
// Focus is on testing real component behavior with real props
```

---

## Coverage Analysis

### Python Test Coverage

**Estimated Coverage:** ~40–50% (moderate gaps in critical paths)

**Well-Tested Areas:**
- Redis operations (`test_redis_queries.py`)
- Contact admin FSM logic (`test_contact_admin.py` — focused on user input parsing)
- State persistence (`test_state_store.py`)
- Telegram auth validation (`test_mini_auth.py`)

**Coverage Gaps (HIGH PRIORITY):**
1. **Workflow execution engine** (`execution/` folder)
   - Files: `execution/core/runner.py`, `execution/core/progress_reporter.py`, `execution/curation/router.py`
   - Status: Minimal to no test coverage
   - Impact: Core business logic (news ingestion → Redis staging → WhatsApp sending) not validated

2. **Async pipeline orchestration** (`webhook/pipeline.py`, `webhook/dispatch.py`)
   - Files: `webhook/pipeline.py` (3-agent Claude orchestration), `webhook/dispatch.py` (WhatsApp sending + approval)
   - Status: No unit/integration tests
   - Impact: Agent coordination, error recovery, approval flow not validated

3. **External integrations**
   - Anthropic API interaction (mocked in tests but no end-to-end validation)
   - Google Sheets sync (`webhook/dispatch.py` fetch, `execution/integrations/sheets_client.py`)
   - WhatsApp delivery (mocked, no real delivery test)

4. **Bot command handlers** (`webhook/bot/routers/`)
   - Files: `webhook/bot/routers/commands.py`, `webhook/bot/routers/callbacks.py`, `webhook/bot/routers/messages.py`
   - Status: No test coverage detected
   - Impact: Telegram command routing, FSM transitions, callback button handling not tested

5. **Error recovery and retries**
   - `execution/core/retry.py` — not directly tested
   - Backoff logic in external API calls not systematized

### TypeScript Test Coverage

**Estimated Coverage:** ~30–40% (component snapshot level, missing integration)

**Well-Tested Areas:**
- Component rendering (`GlassCard.test.tsx`, `Skeleton.test.tsx`, `MenuRow.test.tsx`)
- Utility functions (`format.test.ts` with relative time and duration formatting)

**Coverage Gaps:**
1. **API integration** (`src/hooks/useApi.ts`, `src/lib/api.ts`)
   - Status: Hook exists but no tests for actual SWR fetching behavior
   - Impact: Data loading, error states, caching not validated

2. **Page-level flows**
   - Files: `src/pages/Home.tsx`, `src/pages/News.tsx`, `src/pages/Workflows.tsx`
   - Status: Basic placeholder tests; no user interaction or data flow testing
   - Impact: Multi-component integration, state management, conditional rendering not validated

3. **State management** (if any via Context or local state)
   - `useTelegram()` hook usage not covered
   - Parameter passing between pages not tested

### Test-to-Code Ratio

**Python:**
```
Test files:    ~5,000 lines (33 files)
Source code:   ~15,000+ lines (execution/, webhook/)
Ratio:         ~1:3 (moderate)
```

**TypeScript (mini-app):**
```
Test files:    ~50–100 lines per component/utility
Source code:   ~2,000+ lines
Ratio:         ~1:5–1:10 (sparse)
```

---

## CI/CD Testing Integration

**GitHub Actions:** Tests NOT automatically run on commit (no workflow file for test execution)

**Status:** No CI test workflow detected

**Implication:**
- Tests are manual/local only
- No gating on code quality or test passage
- High risk of merging untested code to main branch

**Recommendation:** Add GitHub Actions workflow to run:
```yaml
- name: Run Python tests
  run: pytest --tb=short

- name: Run TypeScript tests (mini-app)
  run: cd webhook/mini-app && npm test
```

---

## Running Tests Locally

**Python:**
```bash
# Install dependencies
pip install -r requirements.txt

# Run all tests
pytest

# Run specific test file
pytest tests/test_redis_queries.py

# Run with coverage report (requires pytest-cov)
pytest --cov=execution --cov=webhook --cov-report=html
```

**TypeScript (mini-app):**
```bash
# Navigate to mini-app directory
cd webhook/mini-app

# Install dependencies
npm install

# Run tests once
npm run test

# Watch mode
npm run test:watch

# Coverage (if configured)
npm run test -- --coverage
```

---

## Known Issues & Gaps

1. **No pytest plugins for coverage reporting** — `pytest-cov` not in requirements; manual analysis needed

2. **Async test infrastructure limited** — Only `pytest-asyncio`; no fixtures for common async patterns (API responses, timeouts)

3. **No E2E tests** — No Playwright or Cypress setup; mini-app UI flows untested end-to-end

4. **Mock isolation weak** — Global `monkeypatch` usage could cause test interdependencies if fixtures not auto-reset

5. **Test discovery risk** — If test files not named exactly `test_*.py`, they won't auto-discover (fragile convention)

6. **TypeScript test setup minimal** — No vitest config file means reliance on defaults; hard to customize coverage thresholds

---

*Testing analysis: 2026-04-17*
