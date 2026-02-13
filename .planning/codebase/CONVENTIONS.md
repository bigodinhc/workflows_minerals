# Coding Conventions

**Analysis Date:** 2026-02-13

## Naming Patterns

**Files:**
- React components: PascalCase (`SideNav.tsx`, `Button.tsx`, `Card.tsx`)
- API routes: lowercase kebab-case in route directories (`/api/workflows/route.ts`, `/api/news/route.ts`)
- Python scripts: snake_case (`morning_check.py`, `send_daily_report.py`)
- Python classes and modules: snake_case (`platts_client.py`, `claude_client.py`, `StateManager` for classes)
- Configuration files: lowercase with dots (`.eslintrc.mjs`, `tsconfig.json`, `postcss.config.mjs`)

**Functions:**
- TypeScript/JavaScript: camelCase (`handleTrigger`, `formatDistanceToNow`, `fetcher`)
- Python: snake_case (`normalize_text`, `format_price_message`, `retry_with_backoff`)
- React hooks: camelCase with `use` prefix (`usePathname`, `useSWR`)

**Variables:**
- camelCase for mutable state (`selectedRunId`, `isLoadingLogs`, `triggeringId`)
- camelCase for constants in JS/TS (e.g., `WORKFLOWS`, `FINES_KEYS` as uppercase only for whitelists)
- PascalCase for React component props interfaces (`NavItem`)
- SCREAMING_SNAKE_CASE for environment config constants (`TELEGRAM_BOT_TOKEN`, `UAZAPI_URL`)

**Types:**
- TypeScript interfaces: PascalCase (`NavItem`, `ApiResponse`)
- Type aliases: PascalCase
- React component types: implicit via function parameters and return types

## Code Style

**Formatting:**
- Tool: ESLint 9 with Next.js and TypeScript configs
- Config: `dashboard/eslint.config.mjs`
- Uses `eslint-config-next/core-web-vitals` and `eslint-config-next/typescript` presets
- No Prettier detected; ESLint handles linting, formatting deferred to IDE

**Linting:**
- ESLint v9 with flat config (modern ESLint)
- Core configs: `@eslint-config-next/core-web-vitals` and `@eslint-config-next/typescript`
- Ignores: `.next/**`, `out/**`, `build/**`, `next-env.d.ts`
- Python: No centralized linter configured; code should follow PEP 8 by convention

**TypeScript Compiler:**
- Target: ES2017
- Strict mode: enabled
- Module: esnext
- JSX: react-jsx (React 19.2.3 with new JSX transform)
- Module resolution: bundler
- Path aliases configured: `@/*` maps to root directory `./`
- incremental builds enabled for faster rebuilds

## Import Organization

**Order:**
1. Standard library imports (e.g., `import os`, `import sys`, `from datetime import`)
2. Third-party packages (e.g., `from anthropic import Anthropic`, `import React`)
3. Local application imports (e.g., `from execution.core.logger import WorkflowLogger`)
4. Relative imports (e.g., `from .logger import WorkflowLogger`)

**Path Aliases:**
- TypeScript: Use `@/*` alias for root-relative imports: `@/components/ui/button`, `@/lib/utils`
- Avoid relative paths like `../../../` in favor of `@/` imports
- Example: `import { Button } from "@/components/ui/button"`

**Python imports:**
- Use absolute imports with `sys.path.append` when necessary:
  ```python
  sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))
  from execution.core.logger import WorkflowLogger
  ```

## Error Handling

**TypeScript/JavaScript Patterns:**
- Wrap fetch/API calls in try-catch blocks
- Log errors with `console.error()` (currently used in routes and components)
- Return NextResponse error responses with appropriate status codes:
  ```typescript
  if (!token) {
    return NextResponse.json({ error: "Missing Token" }, { status: 500 });
  }
  try {
    // operation
  } catch (error) {
    console.error("Operation Error:", error);
    return NextResponse.json({ error: "Failed to..." }, { status: 500 });
  }
  ```

**Python Patterns:**
- Use try-catch for API calls and file operations
- Log errors through `WorkflowLogger`: `logger.error("Step failed", {"error": str(e)})`
- Raise descriptive exceptions or return error states
- Example from `execution/core/runner.py`:
  ```python
  try:
    # operation
  except Exception as e:
    logger.critical("Workflow failed", {"error": str(e)})
    return {"success": False, "run_id": logger.run_id, "error": str(e)}
  ```

## Logging

**Framework:**
- TypeScript/JavaScript: `console.error()` for error logging (found in: `dashboard/app/page.tsx`, `dashboard/app/api/workflows/route.ts`)
- Python: Custom `WorkflowLogger` class (`execution/core/logger.py`) using JSON-formatted logs

**Patterns:**

**Python structured logging (`WorkflowLogger`):**
```python
from execution.core.logger import WorkflowLogger

logger = WorkflowLogger("workflow_name")
logger.info("Processing started", {"items": 10})
logger.error("Step failed", {"error": str(e)})
logger.critical("Workflow failed", {"error": "message"})
```

**Logs structure:**
- Location: `.tmp/logs/{workflow_name}/{run_id}.json`
- Format: JSON with timestamp, workflow, run_id, step, level, message, data
- Each entry is immutable and appended to file

**JavaScript error logging:**
- Use `console.error()` only in error paths
- Include context: `console.error("GitHub API Error:", error)`
- Routes should log errors before returning error responses

## Comments

**When to Comment:**
- Explain complex business logic (e.g., market data whitelist logic in `morning_check.py`)
- Document non-obvious algorithm choices
- Add context for temporary workarounds or TODOs
- Do NOT comment obvious code (e.g., `// increment counter`)

**JSDoc/TSDoc:**
- Python uses module-level docstrings and function docstrings (Google style):
  ```python
  def run_workflow(directive: str, inputs: Optional[dict] = None) -> dict:
      """
      Execute a workflow defined by a directive.

      Args:
          directive: Name of the directive (without path/extension)
          inputs: Input data for the workflow

      Returns:
          dict with 'success', 'outputs', and 'logs'
      """
  ```
- TypeScript: Minimal JSDoc; types are self-documenting with TypeScript

## Function Design

**Size:**
- Keep functions under 50 lines where possible
- React components should be focused on a single responsibility
- Example: `handleTrigger()` in `dashboard/app/page.tsx` is ~15 lines

**Parameters:**
- Use object parameters for functions with >2 args
- Prefer destructuring in function signatures
- Example: `const { searchParams } = new URL(req.url)`

**Return Values:**
- Consistent return types (TypeScript enforces this)
- API routes return `NextResponse.json()` for consistency
- Python workflows return dict with: `{"success": bool, "outputs": any, "logs": list, ...}`

## Module Design

**Exports:**
- Named exports preferred over default exports
- Example from `dashboard/components/ui/card.tsx`:
  ```typescript
  export { Card, CardHeader, CardFooter, CardTitle, CardAction, CardDescription, CardContent }
  ```

**Barrel Files:**
- Not heavily used; components export directly
- UI components in `dashboard/components/ui/` are individual files
- Python modules use `__all__` for public API:
  ```python
  __all__ = ["run_workflow", "create_step_executor"]
  ```

**Immutability:**
- React state updates use spread operator: `{ ...user, name }`
- Python: Functions return new objects rather than mutating arguments
- API payloads are immutable JSON structures

## Data Flow Patterns

**API Response Format:**
- Consistent JSON structure with success/error fields
- Example from workflows route:
  ```typescript
  return NextResponse.json(simplifiedRuns); // Array of objects
  return NextResponse.json({ error: "Failed" }, { status: 500 });
  ```

**Component Data Fetching:**
- SWR for client-side data fetching:
  ```typescript
  const { data: runs, error, mutate } = useSWR("/api/workflows", fetcher, { refreshInterval: 10000 });
  ```

**Error Boundaries:**
- Frontend: Check for error state in render: `const isOnline = !error`
- Backend: All API routes check for required env vars and return 500 on missing config

## TypeScript Configuration Details

**Compiler flags:**
- `strict: true` - enables strict type checking
- `noEmit: true` - only type-check, don't emit JS (Next.js handles emit)
- `esModuleInterop: true` - allows import of CommonJS modules
- `resolveJsonModule: true` - import JSON files as modules
- `isolatedModules: true` - each file is independent module (required for Babel)

**Path resolution:**
- `moduleResolution: "bundler"` - modern resolution algorithm for bundlers
- `paths: { "@/*": ["./*"] }` - enables `@/` imports throughout codebase

## Style & UI Conventions

**Tailwind CSS:**
- Used throughout dashboard for utility-first CSS
- Custom colors for neon cyberpunk theme: `#00FF41` (neon green), `#0a0a0a` (black bg)
- Responsive utilities: `md:` prefix for tablet+ breakpoints, mobile-first approach
- Font: JetBrains Mono monospace via `@fontsource/jetbrains-mono`

**Component Library:**
- Radix UI primitives wrapped with Tailwind styling
- shadcn/ui components for consistency (`Card`, `Button`, `Sheet`, `ScrollArea`)
- Icon library: Lucide React for consistent icon set

---

*Convention analysis: 2026-02-13*
