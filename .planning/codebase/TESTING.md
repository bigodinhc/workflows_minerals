# Testing Patterns

**Analysis Date:** 2026-02-13

## Test Framework

**Runner:**
- No centralized test runner detected for TypeScript/React code
- Python: pytest assumed as standard (via `tests/test_format.py` pattern)
- Config: Not configured in dashboard or execution directories

**Assertion Library:**
- Not configured; no test files found in source directories (only in node_modules via zod)

**Run Commands:**
```bash
# No test commands currently configured in package.json
# Dashboard package.json only has: dev, build, start, lint
npm run lint                          # Run ESLint

# Python tests (manual execution assumed)
python tests/test_format.py           # Format validation test
```

## Test File Organization

**Location:**
- Python tests: `tests/` directory at root level
- TypeScript/React: No test files configured (test capability missing)
- Recommendation: Co-locate tests with implementation (`dashboard/app/__tests__/` or `dashboard/components/__tests__/`)

**Naming:**
- Python: `test_*.py` pattern (e.g., `test_format.py`)
- TypeScript: Would use `.test.ts` or `.spec.ts` suffix (not configured)

**Structure:**
```
tests/
├── test_format.py          # Format validation for price data
```

## Test Structure

**Python Test Example:**

From `tests/test_format.py`:
```python
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from execution.scripts.send_daily_report import format_price_message

# Mock data
mock_prices = [
    {"month": "SEP/25", "price": 105.40, "change": 0.10, "pct_change": 0.09},
    # ... more items
]

print("--- TESTING FORMAT ---")
message = format_price_message(mock_prices)
print(message)
print("----------------------")
```

**Patterns:**
- Setup: Import modules and test data using sys.path manipulation
- Execution: Call function with known inputs
- Assertion: Print output for manual inspection (no assertions library used)
- No teardown: Simple test scripts

## Mocking

**Framework:**
- None configured; manual mocking via test data
- SWR mocking in React components (implicit via fetch override)

**Patterns:**

**Python mocking (mock data injection):**
```python
mock_prices = [
    {"month": "SEP/25", "price": 105.40, "change": 0.10, "pct_change": 0.09},
]
message = format_price_message(mock_prices)
```

**React data fetching (SWR pattern):**
```typescript
const fetcher = (url: string) => fetch(url).then((res) => res.json());
const { data: runs, error, mutate } = useSWR("/api/workflows", fetcher, { refreshInterval: 10000 });
```

**What to Mock:**
- External API calls (GitHub API, Google Sheets, etc.)
- Database operations
- File I/O for deterministic testing
- Anthropic API responses for AI workflows

**What NOT to Mock:**
- Core business logic (formatting, data transformation)
- State management (StateManager, logger)
- Router/navigation in Next.js (use test utility, not mock)
- Internal utility functions (cn, format functions)

## Fixtures and Test Data

**Test Data:**
- Python: Inline mock objects (dictionaries with price data)
- No fixture framework (pytest fixtures) configured
- Hard-coded test data in test files

**Location:**
- `tests/` directory for Python test data
- No established fixtures directory (recommend `tests/fixtures/` or `tests/__fixtures__/`)

**Pattern for structured test data:**
```python
# Mock data structure for market reports
MOCK_WORKFLOW_RUN = {
    "id": 12345,
    "status": "completed",
    "conclusion": "success",
    "name": "morning_check",
    "created_at": "2026-02-13T08:30:00Z",
    "run_number": 42
}
```

## Coverage

**Requirements:**
- Not enforced; no coverage tool configured
- Recommendation: Target 80%+ coverage once testing framework is added

**View Coverage:**
```bash
# Once pytest is configured:
pytest --cov=dashboard --cov=execution --cov-report=html
# Once vitest/jest is configured:
npm test -- --coverage
```

## Test Types

**Unit Tests:**
- Scope: Individual functions and utilities
- Approach: Test expected input/output transformations
- Example targets:
  - `dashboard/lib/utils.ts` - `cn()` function for class merging
  - `execution/core/logger.py` - JSON log formatting
  - `execution/core/state.py` - state get/set operations
  - Format functions: `format_price_message()`, `format_line()` from `morning_check.py`

**Integration Tests:**
- Scope: API routes + external services (GitHub, Google Sheets, etc.)
- Approach: Mock external APIs, test full request/response cycle
- Example targets:
  - `dashboard/app/api/workflows/route.ts` - GET workflows, POST trigger
  - `dashboard/app/api/news/route.ts` - draft approval flow
  - `dashboard/app/api/logs/route.ts` - fetch GitHub Actions logs
  - `execution/core/runner.py` - workflow execution with mocked steps

**E2E Tests:**
- Framework: Not implemented (Playwright recommended for Next.js)
- Recommended critical flows:
  - Dashboard: Load home page, trigger workflow, view logs
  - News workflow: Approve news draft, verify dispatch
  - Daily report: Fetch market data, format message

## Manual Testing Approach

**Current practice:**
- Python: Run scripts directly: `python3 execution/scripts/morning_check.py`
- React: Manual browser testing via `npm run dev`
- API: Manual fetch/curl testing (evident from comments in `app/api/news/route.ts`)

**Missing automated test execution:**
- No `npm test` script configured
- No CI test running in GitHub Actions
- Test files are manual/exploratory only

## Testing Configuration Gaps

**Not configured:**
- No test runner (pytest, vitest, jest)
- No assertion library (no unittest, no chai/expect)
- No mocking framework (no jest.mock, no unittest.mock)
- No coverage tool (pytest-cov, c8, nyc)
- No E2E framework (no Playwright, Cypress)

**Recommended setup for quality focus:**

**For TypeScript/React:**
```json
// Add to dashboard/package.json devDependencies:
"vitest": "^1",
"@testing-library/react": "^14",
"@testing-library/jest-dom": "^6"
```

**For Python:**
```bash
# Add to requirements.txt or pyproject.toml:
pytest>=7.0
pytest-cov>=4.0
```

## Common Patterns to Avoid

**Anti-patterns observed:**
- `console.error()` logging in production code instead of structured logging
- No error boundary testing (React error paths not tested)
- State persistence (`StateManager`) not validated with tests
- API response formats assumed correct (no schema validation tests)

## Console Statements

**Current usage found:**
- `console.error()` in: `dashboard/app/page.tsx`, `dashboard/app/api/workflows/route.ts`, `dashboard/app/api/logs/route.ts`, `dashboard/app/api/news/route.ts`, `dashboard/app/api/contacts/route.ts`
- Should be replaced with structured logging or error monitoring (Sentry, etc.)

**Python logging:**
- `print()` statements in `WorkflowLogger` for visibility
- All important state logged via `.tmp/logs/{workflow}/{run_id}.json`

---

*Testing analysis: 2026-02-13*
