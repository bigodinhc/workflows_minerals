# Coding Conventions

**Analysis Date:** 2026-04-22

Multi-language repository: Python (execution, webhook, tests), JavaScript/ESM (Apify actors, root apify-client), and TypeScript/React (dashboard, webhook mini-app). Conventions differ per subsystem — follow the right set for the file you are editing.

## Naming Patterns

**Python — `execution/`, `webhook/`, `tests/`:**
- Modules and packages: `snake_case` — `event_bus.py`, `delivery_reporter.py`, `callback_data.py`.
- Functions and local variables: `snake_case` — `_get_supabase_client`, `record_crash`, `try_claim_alert_key`.
- Classes: `PascalCase` — `EventBus`, `WorkflowLogger`, `ContactsRepo`, `DeliveryReport`.
- Constants and module-level config: `UPPER_SNAKE_CASE` — `WORKFLOW_NAME`, `REPORT_TYPE`, `FINES_KEYS`, `_VALID_LEVELS`, `_EVENTS_CHANNEL_DENYLIST`.
- Private/internal: single-underscore prefix — `_active_bus`, `_build_telegram_client`, `_StdoutSink`, `_MainChatSink`.
- Aiogram CallbackData classes always end in an action/noun — `CurateAction`, `DraftAction`, `ReportDownload`, `ContactBulk` (see `webhook/bot/callback_data.py`).

**JavaScript — `actors/*/src/**`:**
- Files: `camelCase.js` — `eventBus.js`, `applyFilters.js`, `articlePage.js`, `dates.js`. Entry points are always `src/main.js`.
- Functions and variables: `camelCase` — `generateRunId`, `initSupabase`, `collectFlashBanner`, `parsePublishedDate`.
- Classes: `PascalCase` — `EventBus`.
- Constants: `UPPER_SNAKE_CASE` — `VALID_LEVELS`, `DEFAULT_EXCLUDES`.
- Private class fields: underscore prefix (`this._supabase`, `this._runId`, `this._workflow`).

**TypeScript/React — `dashboard/**`:**
- Component files: `PascalCase.tsx` — `SideNav`, `DeliveryReportView`. UI primitives follow shadcn lowercase convention in `components/ui/` (`button.tsx`, `card.tsx`, `dropdown-menu.tsx`).
- Route files: Next.js App Router convention — `page.tsx`, `layout.tsx`, `route.ts` inside segment dirs (`app/contacts/page.tsx`, `app/api/contacts/route.ts`).
- Hooks/utils: `camelCase` in `lib/` (e.g., `utils.ts`).
- CSS classes from Tailwind; variance helpers via `class-variance-authority` and `clsx`/`tailwind-merge`.

**Workflow and event names (strings):**
- `snake_case` identifiers shared across Python and JS — `morning_check`, `baltic_ingestion`, `platts_ingestion`, `platts_reports`, `platts_scrap_full_news`, `rationale_news`, `rebuild_dedup`, `watchdog`.
- Event names are `snake_case` verbs/phrases — `cron_started`, `cron_finished`, `cron_crashed`, `cron_missed`, `step`, `api_call`, `delivery_summary`.

## File Organization

**Many small files > few large files.** User-level rule; mostly honored but violated by five files >300 lines. Largest offenders: `execution/core/delivery_reporter.py` (537), `execution/scripts/baltic_ingestion.py` (415), `execution/core/event_bus.py` (393), `webhook/bot/routers/commands.py` (378), `execution/core/progress_reporter.py` (363). Split when touching these.

**Feature-based layout, not type-based:**
- `execution/core/` — reusable primitives (event bus, state store, progress reporter, cron parser, retry, logger, sentry init).
- `execution/integrations/` — one file per external service (`apify_client.py`, `baltic_client.py`, `claude_client.py`, `lseg_client.py`, `platts_client.py`, `supabase_client.py`, `telegram_client.py`, `uazapi_client.py`, `contacts_repo.py`).
- `execution/scripts/` — one script per cron directive (`baltic_ingestion.py`, `morning_check.py`, `platts_ingestion.py`, `platts_reports.py`, `send_daily_report.py`, `watchdog_cron.py`, `rebuild_dedup.py`).
- `webhook/bot/routers/` — one file per domain (`callbacks_curation.py`, `callbacks_contacts.py`, `callbacks_reports.py`, `callbacks_workflows.py`, `callbacks_menu.py`, `callbacks_queue.py`, `commands.py`, `messages.py`, `onboarding.py`, `settings.py`).
- Actor source: one directory per responsibility — `auth/`, `download/`, `filters/`, `grid/`, `lib/`, `notify/`, `persist/`, `util/` (see `actors/platts-scrap-reports/src/`).

## Module Boundaries

**Layered import rule** (enforce when adding code):
- `execution.scripts.*` is top-level; it imports from `execution.core.*` and `execution.integrations.*` only.
- `execution.integrations.*` imports from `execution.core.*` only, never from `execution.scripts.*`.
- `execution.core.*` is leaf — only stdlib, third-party, and sibling core modules.
- `webhook/bot/routers/*` imports `webhook/bot/*` helpers and `execution.core.*`, never `execution.scripts.*`.
- Dashboard TS and the webhook Python each hit Supabase independently — do not cross-import.

**Avoid circular imports with lazy loading.** `execution/core/state_store.py::_current_run_id` imports `event_bus.get_current_bus` inside the function, not at module scope, because `event_bus` imports `state_store` via `@with_event_bus`. Use the same pattern whenever core modules need to cross-reference.

## Immutability

User rule (CLAUDE.md): never mutate, always spread.

- Python emit patterns build fresh dicts: `event_dict = {...}` inside `EventBus.emit` (`execution/core/event_bus.py:129`). Sinks receive dicts by reference but do not mutate them — `_SupabaseSink.emit` builds a new `row = {k: v for k, v in event_dict.items() if k != "ts"}` rather than `event_dict.pop("ts")`.
- JS emit composes with `{ ts: ..., ...row }` spread (`actors/*/src/lib/eventBus.js:63`).
- Existing violations: `reporter._message_id = initial.message_id` and `reporter._pending_card_state = []` in `execution/scripts/platts_reports.py:66-68` mutate a `ProgressReporter` after construction. Do not extend this pattern; pass into the constructor instead.

## Error Handling

**Scripts use the `@with_event_bus(workflow_name)` decorator.** `execution/core/event_bus.py:335` wraps `main()` and on any exception:
1. Emits `cron_crashed` with `label=f"{type(exc).__name__}: {str(exc)[:100]}"`, `level="error"`, `detail={"exc_type": ..., "exc_str": str(exc)[:500]}`.
2. Calls `state_store.record_crash(workflow, exc_text)` so the watchdog sees the attempt.
3. Calls `sentry_sdk.capture_exception(exc)` with breadcrumbs already on the scope.
4. Re-raises so the GitHub Actions run is marked failed.

Never swallow exceptions at the top level — let the decorator handle them. Catch narrowly (e.g. `except phonenumbers.NumberParseException`) when recovering. If `state_store.record_crash` itself raises, the decorator's nested try/except ensures the *original* exception is re-raised, not the secondary one.

**Telemetry sinks are never-raise.** Sink failures are logged with `logger.warning(...)` and swallowed — see `EventBus.emit` try/except per sink (`event_bus.py:140-145`). The JS actor bus (`actors/*/src/lib/eventBus.js:62-89`) does the same, including circular-ref fallback that replaces `detail` with the string `"[unserializable]"`. Apply this rule to any new sink.

**Exception hierarchy per domain.** `execution/integrations/contacts_repo.py` defines `ContactNotFoundError(Exception)`, `ContactAlreadyExistsError(Exception)` (carries `self.existing`), and `InvalidPhoneError(ValueError)`. Prefer domain-specific types over bare `Exception`.

**`raise ... from e` when wrapping.** `raise InvalidPhoneError(f"could not parse phone: {e}") from e` (`contacts_repo.py:67`). Preserves the cause chain for Sentry.

**Actor error path.** Apify actors must emit `cron_crashed` even on pre-run validation failures (e.g., missing input) before calling `Actor.exit(1)` — see fix `2168615` / `fa0bccb`. Wrap `Actor.init()` + input validation in a try/catch that emits `cron_crashed` with `detail.apify_run_id = process.env.ACTOR_RUN_ID`.

## Logging & Observability

**EventBus is the canonical logging path for scripts.** `execution/core/event_bus.py`. Every cron script:

```python
from execution.core.event_bus import with_event_bus, get_current_bus

@with_event_bus("morning_check")
def main():
    bus = get_current_bus()
    bus.emit("step", label="Baixando dados Platts")
    bus.emit("api_call", label="platts.get_report_data",
             detail={"duration_ms": round((time.time() - t0) * 1000), "rows": len(items)})
```

Standard emission shape (must match across Python and JS):
- `workflow` — snake_case string
- `run_id` — 8-char lowercase hex, auto-generated via `secrets.token_hex(4)` / `crypto.randomBytes(4).toString('hex')`
- `trace_id` — inherits from `TRACE_ID` env, constructor arg, or defaults to `run_id`
- `parent_run_id` — inherits from `PARENT_RUN_ID` env or constructor arg; null when absent
- `level` — one of `info`, `warn`, `error`; anything else coerced to `info`
- `event` — `cron_started`, `cron_finished`, `cron_crashed`, `cron_missed`, `step`, `api_call`, `delivery_summary`, or a new domain verb
- `label` — short human description (ASCII + Portuguese OK, truncated to 80 chars in the Telegram card renderer)
- `detail` — JSON-serializable dict; include `apify_run_id`, `duration_ms`, `rows`, `items`, counts, `exc_type`/`exc_str` for crashes

**Trace propagation into Apify actors** (Phase 4). Python scripts inject `run_input["trace_id"] = current.trace_id` and `run_input["parent_run_id"] = current.run_id` before calling `client.run_actor(...)` — see `tests/test_platts_ingestion_trace.py:38-40` for the exact pattern. The actor reads `input.trace_id` / `input.parent_run_id` and passes them to `new EventBus({workflow, traceId, parentRunId})` (`actors/platts-scrap-full-news/src/main.js:65-69`). Every actor run's first emit is `cron_started` with `detail = { apify_run_id: process.env.ACTOR_RUN_ID ?? null }` so Supabase rows are linkable back to Apify's UI (fix `26ab199`).

**`WorkflowLogger` (`execution/core/logger.py`) is legacy.** Still imported by some scripts (`morning_check.py:19`, `baltic_ingestion.py:31`). Prefer EventBus emits for anything downstream tooling should see; `WorkflowLogger.info()` writes only to `.tmp/logs/<workflow>/<run_id>.json` and `print()`.

**Python stdlib `logging`** is used for internal library-style warnings in `execution/core/*` and `webhook/*` — `logger = logging.getLogger(__name__)` at module top (see `state_store.py:14`, `event_bus.py:22`, `cron_parser.py:12`, `webhook/dispatch.py:27`). Never use `print()` for anything other than stdout reports (CLI summary) or pre-init stderr warnings.

**JS actors** log flow via `crawlee.log.info/warn/error` (`actors/platts-scrap-full-news/src/main.js:38`) and lifecycle via `EventBus.emit()`. `console.log` is reserved for the EventBus stdout sink — do not use it elsewhere in actor code. `console.warn` is the sink-failure channel.

## Import Organization

**Python:**

```python
# 1. Future imports
from __future__ import annotations

# 2. Standard library (alphabetical)
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Optional

# 3. Third-party (alphabetical)
import phonenumbers
import pytest
from dotenv import load_dotenv

# 4. First-party
from execution.core.event_bus import with_event_bus, get_current_bus
from execution.core.logger import WorkflowLogger
from execution.integrations.platts_client import PlattsClient
```

Scripts use `sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))` near the top (e.g. `execution/scripts/morning_check.py:16`, `execution/scripts/platts_reports.py:17`) to support `python execution/scripts/X.py`. Keep this idiom; `tests/conftest.py:8-12` handles the same for tests.

**TypeScript (dashboard):**
- Path alias `@/*` maps to repo root (`dashboard/tsconfig.json:22`).
- Use `import type` for types-only imports — `import type { Metadata } from "next"`.
- Side-effect imports (CSS, fonts) before value imports: `import "@fontsource/jetbrains-mono/400.css"` then `import { SideNav } from "@/components/layout/SideNav"`.

**JavaScript (actors):** ESM only (`"type": "module"` in `package.json`). Named imports, relative paths with `.js` extension — `import { EventBus } from './lib/eventBus.js'`.

## Code Style

**Python:** No linter configured at repo root (no `ruff.toml`, no `[tool.ruff]` in `pyproject.toml`, no `black` config, no `.pre-commit-config.yaml`). Follow PEP 8 by eye. Minimum Python is 3.9 (Railway, `webhook/pyproject.toml:9`); tests target 3.10 (`python-version: '3.10'` in every GH workflow). `webhook/pyproject.toml` declares only build metadata + Railway start command.

**JavaScript (actors):** Prettier + ESLint enforced per-actor.
- `.prettierrc`: `{ printWidth: 120, tabWidth: 4, singleQuote: true }` — 4-space indent, single quotes, 120-column.
- `eslint.config.mjs` composes `@apify/eslint-config/js.js` with `eslint-config-prettier` (last wins, disables stylistic rules that conflict with Prettier).
- Scripts per actor: `npm run lint`, `npm run lint:fix`, `npm run format`, `npm run format:check`.

**TypeScript (dashboard):** ESLint via `eslint-config-next` (core-web-vitals + typescript presets, `dashboard/eslint.config.mjs`). `tsconfig.json` has `"strict": true` — no `any` shortcuts.

**Webhook mini-app:** Vite + TS (`webhook/mini-app/`). Own `tsconfig.json`; uses same ESLint toolchain.

## Environment & Configuration

**Secrets live in `.env`** at repo root. `.env.example` lists keys only (no values). `.env` is gitignored.

**Canonical env vars:**
- Observability: `SENTRY_DSN`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (preferred) or `SUPABASE_KEY` (legacy), `REDIS_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_EVENTS_CHANNEL_ID`, `TRACE_ID`, `PARENT_RUN_ID`.
- Integrations: `APIFY_API_TOKEN`, `PLATTS_USERNAME`/`PLATTS_PASSWORD`, `AZURE_TENANT_ID`/`AZURE_CLIENT_ID`/`AZURE_CLIENT_SECRET`/`AZURE_TARGET_MAILBOX`, `ANTHROPIC_API_KEY`, `LSEG_APP_KEY`/`LSEG_USERNAME`/`LSEG_PASSWORD`, `UAZAPI_URL`/`UAZAPI_TOKEN`, `IRONMARKET_API_KEY`.
- GitHub: `GITHUB_TOKEN`, `GITHUB_OWNER`, `GITHUB_REPO`.

**Supabase key handling:** `execution/core/event_bus.py:50` accepts either `SUPABASE_SERVICE_ROLE_KEY` (preferred) or `SUPABASE_KEY` (legacy). New code must use `SUPABASE_SERVICE_ROLE_KEY`. JS client side only reads `SUPABASE_SERVICE_ROLE_KEY` (`actors/*/src/lib/eventBus.js:24`). Fix `3ce2d29` standardized the env-var name across the contacts repo.

**Loading:** Python scripts call `load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))` explicitly (`platts_reports.py:14-16`). Webhook and actors read `process.env`/`os.getenv` directly.

**Two `requirements.txt`:** root `requirements.txt` (GitHub Actions cron jobs) and `webhook/requirements.txt` (Railway Docker bot). Keep them in sync when adding a shared dep — both list `aiogram>=3.4.0,<4.0`, `supabase>=2.0.0,<3.0`, `sentry-sdk[aiohttp]>=2.0.0,<3.0.0`, `phonenumbers>=8.13,<9.0`. Memory rule (`~/.claude/projects/.../MEMORY.md`): sync both files.

**Python install on this machine:** use `uv pip install -r requirements.txt` — system pip is broken from a Python 3.14 pyexpat dylib issue.

## Aiogram v3 Conventions

**Callback data is typed, not string-split.** Every inline keyboard action has a `CallbackData` subclass in `webhook/bot/callback_data.py`. Emit with `.pack()`:

```python
from bot.callback_data import WorkflowRun, ContactPage

keyboard = InlineKeyboardMarkup(inline_keyboard=[[
    InlineKeyboardButton(text="▶️ Run", callback_data=WorkflowRun(workflow_id="morning_check.yml").pack()),
]])
```

Handler decorators filter on the factory: `@router.callback_query(WorkflowRun.filter())`, arg-unpack via an injected `callback_data: WorkflowRun` parameter (aiogram resolves it). Never hand-build `"workflow_run:foo"` strings — that pattern was retired in fix `7d40506`.

**`parse_mode=None` for runtime-composed text.** Workflow names contain underscores (`morning_check`, `platts_reports`) which Markdown parses as italics. Use `parse_mode=None` on any reply that interpolates workflow/event strings — the `/tail` command uses this throughout (`webhook/bot/routers/commands.py:81,94,101,111,120,127,135,147,156`). Only use `parse_mode="MarkdownV2"` when the entire message is author-controlled and fully escaped. `_EventsChannelSink` hard-codes `parse_mode=None` for the same reason (`event_bus.py:287,294`).

**FSM states** live in `webhook/bot/states.py` per flow — `AdjustDraft.waiting_feedback`, `RejectReason.waiting_reason`. FSM handlers await `state.set_state(X.Y)`, `state.update_data(...)`, `state.clear()` — never read/write raw context dicts.

## Supabase Migrations

Located in `supabase/migrations/`. Filename: `YYYYMMDD_<short_name>.sql` (ISO date prefix for natural ordering). Example: `20260418_event_log.sql`, `20260419_event_log_rls.sql`, `20260422_contacts.sql`.

Rules (from `supabase/migrations/README.md`):
- Use `create table if not exists` — re-applies must be idempotent.
- Declare indexes, triggers, RLS, and comments in the same file as the table.
- No `drop` statements without an explicit rollback policy.
- `alter table ... enable row level security;` on any new table. Service role bypasses RLS; no anon policies unless explicitly required.
- Log applied status in the README table (`Applied to dev` / `Applied to prod` columns with ✅ YYYY-MM-DD).
- Use lowercase SQL keywords (`create`, `insert`, `check`), per `20260422_contacts.sql` convention.

## Git Conventions

**Conventional commits** — `<type>(<scope>): <subject>` format enforced by habit (see `git log --oneline`).

Active types: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `perf`, `ci`. Scope is the subsystem (`actors`, `bot`, `contacts`, `tail`, `observability`, `deps`, `queue`, `mini_api`, `plan`, `spec`).

Examples from recent history:
- `feat(observability): delivery_reporter emits delivery_summary to event bus`
- `fix(tail): use parse_mode=None + expand known workflows`
- `fix(actors): emit cron_crashed on pre-run Actor.exit guards`
- `chore(contacts): retire Google Sheets artifacts after Supabase migration`
- `docs(spec): trace_id propagation from Python crons to Apify actors`

Claude-Code attribution footers are disabled globally (`~/.claude/settings.json`). Do not add them.

## Docs-as-Code

Design documents live in `docs/superpowers/`:
- `specs/` — design docs dated `YYYY-MM-DD-<name>-design.md` (e.g. `2026-04-21-observability-unified-design.md`).
- `plans/` — implementation plans dated `YYYY-MM-DD-<name>-plan.md` (e.g. `2026-04-22-observability-trace-id-apify-propagation-plan.md`).
- `followups/` — post-merge action items dated `YYYY-MM-DD-<feature>-followups.md` (e.g. `2026-04-22-observability-trace-id-apify-followups.md`).

Directives (operational SOPs consumed at runtime) live in `directives/`, with templates in `directives/_templates/` (see `AGENT.md` for the 3-layer architecture).

Planning artifacts during an active task live in `.planning/codebase/` (this file is one of them). `AGENT.md` mirrors `CLAUDE.md` / `AGENTS.md` / `GEMINI.md` so any AI environment picks up the same agent instructions.

## Function Design

- Target <50 lines per function. Scripts regularly exceed this (e.g. `platts_reports._run_with_progress` ~140 lines, `baltic_ingestion.format_whatsapp_message` sprawls). Extract phase-level helpers when touching these files.
- Prefer keyword-only arguments for >2 parameters: `EventBus.emit(event, label="", detail=None, level="info")`.
- Return typed values: `Optional["EventBus"]`, `Optional[dict]`, `list[DeliveryResult]`. Use `from __future__ import annotations` for forward refs (`contacts_repo.py:9`, `conftest.py:2`).
- Factories for tests: return a closure with defaulted kwargs — see `conftest.py:33-81` (`mock_callback_query`, `mock_message`, `fsm_context_in_state`).

## Dataclasses & Models

- Python: `@dataclass` from stdlib — `Contact`, `DeliveryResult`, `DeliveryReport` in `execution/core/delivery_reporter.py`; `@dataclass` for `Contact` in `execution/integrations/contacts_repo.py`. Use `asdict()` for JSON serialization.
- TypeScript: interfaces or `type` aliases; no runtime validation library in use yet (user's global pattern suggests zod — not adopted here).

## Idempotency — daily reports (split-lock pattern)

Daily-report workflows (`baltic_ingestion`, `morning_check`) use **two** Redis keys, not one:

- `daily_report:inflight:{REPORT_TYPE}:{date}` — 20min TTL. Acquired via `try_claim_alert_key` after data validation; released in `finally` on exit. Prevents two crons from broadcasting the same report in parallel. Auto-expires after a crash.
- `daily_report:sent:{REPORT_TYPE}:{date}` — 48h TTL. Written via `set_sent_flag` **only** after the full broadcast (IronMarket POST + WhatsApp dispatch) succeeds. Checked via `check_sent_flag` at the start of every run.

### Claim ordering rule

Early-exits that precede any side effect (source data missing, stale, or incomplete) must not touch either key. Early-exits that occur after Phase 3 (lock acquired) release the lock in `finally` but do **not** set the sent flag — the next cron retries cleanly.

### Anti-pattern (the bug of 2026-04-22)

Using a single long-TTL `SET NX EX` key as both the concurrency guard and the sent flag. A pre-processing early-exit then holds the key for the full TTL, blocking all retries on the same day. The Sheets→Supabase migration (`df15d9aa`) regressed this by compacting the legacy `check_daily_status` + `mark_daily_status` pair into one atomic call; the split-lock pattern above is the correct replacement.

### When to deviate

If operational experience shows mid-broadcast crashes happen often enough that duplicate WhatsApp messages become a real complaint, add per-contact dedup (Redis set of delivered phone numbers under a 48h key). `DeliveryReporter` already tracks per-contact results, so this is ~20 lines. Not needed today.

### Reference

- Design doc: `docs/superpowers/specs/2026-04-22-idempotency-claim-ordering-fix-design.md`
- Implementation plan: `docs/superpowers/plans/2026-04-22-idempotency-split-lock-plan.md`

---

*Convention analysis: 2026-04-22*
