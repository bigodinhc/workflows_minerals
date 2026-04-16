# Webhook Refactor + Workflow Trigger via Telegram Bot

**Date:** 2026-04-16
**Status:** Approved

## Context

`webhook/app.py` has grown to 1,870 lines with 51 functions. While it works and is organized with section comments, new features (like workflow triggering) will add more callback handlers and logic. Refactoring first makes the codebase easier to extend.

The second goal is to let users trigger GitHub Actions workflows (currently only available via the Next.js dashboard) directly from the Telegram bot, with real-time status tracking.

## Part 1: Refactor webhook/app.py

### Goal

Split app.py into domain-focused modules. Keep app.py as a thin router (~400 lines) with Flask config, env loading, HTTP routes, and the `/webhook` dispatcher.

### Already extracted modules (no changes)

- `contact_admin.py` (180 lines) — contact add flow state machine
- `query_handlers.py` (179 lines) — /queue, /history, /rejections, /stats formatting
- `redis_queries.py` (226 lines) — Redis feedback persistence
- `digest.py` (68 lines) — message formatting utilities

### New modules to extract

| Module | Responsibility | Functions to move |
|---|---|---|
| `telegram.py` | Telegram Bot API wrapper | `telegram_api`, `send_telegram_message`, `edit_message`, `answer_callback`, `finalize_card`, `send_approval_message` |
| `pipeline.py` | Claude AI 3-agent pipeline | `call_claude`, `run_3_agents`, `run_adjuster`, `process_news_async`, `process_adjustment_async` |
| `dispatch.py` | WhatsApp delivery + contacts | `send_whatsapp`, `_send_whatsapp_raising`, `get_contacts`, `process_approval_async`, `process_test_send_async` |
| `callback_router.py` | Callback query dispatcher | `handle_callback` (the ~430 line switch) |
| `reports_nav.py` | /reports Telegram navigation | `_reports_show_types`, `_reports_show_years`, `_reports_show_months`, `_reports_show_month_list` |
| `status_builder.py` | /status workflow health | `_format_status_lines`, `_build_status_message` |

### What stays in app.py

- Flask app instance and configuration
- Environment variable loading
- In-memory state dicts (`ADJUST_STATE`, `REJECT_REASON_STATE`, `SEEN_ARTICLES`)
- `begin_reject_reason`, `consume_reject_reason` (tied to in-memory state)
- Redis drafts helpers (`drafts_get`, `drafts_set`, `drafts_contains`, `drafts_update`)
- HTTP route handlers (`/webhook`, `/health`, `/store-draft`, `/seen-articles`, `/preview`, `/admin/register-commands`)
- The `/webhook` handler dispatches to `callback_router.handle_callback` for button presses and handles text commands inline (these are short — each is 5-15 lines)

### Dependency direction

Modules import from each other as needed (e.g., `callback_router` imports from `telegram`, `pipeline`, `dispatch`). No circular dependencies — `telegram.py` is a leaf module with no webhook imports.

### Migration approach

Pure extraction — move functions, update imports, no behavior changes. Each module extraction is independently testable: extract, run tests, confirm green, move to next.

## Part 2: Workflow Trigger via Telegram Bot

### Workflow catalog

Static Python dict mirroring the dashboard's `WORKFLOW_CATALOG`:

```
MORNING CHECK     → morning_check.yml    — Platts iron ore prices
BALTIC EXCHANGE   → baltic_ingestion.yml — Baltic Exchange BDI + routes
DAILY SGX REPORT  → daily_report.yml     — LSEG/SGX futures
PLATTS INGESTION  → market_news.yml      — Platts news curation pipeline
PLATTS REPORTS    → platts_reports.yml   — PDF report scraping
```

### User interaction flow

1. User types `/workflows` or clicks "Workflows" in `/s` menu
2. Bot shows list of all workflows with inline buttons. Each button shows workflow name + last run status indicator (checkmark/X/clock) + time ago
3. User taps a workflow button
4. Bot edits the message to "Disparando MORNING CHECK..." with a [Cancelar] button
5. Bot calls GitHub Actions API to trigger `workflow_dispatches`
6. Bot edits to "MORNING CHECK rodando..." (spinner emoji)
7. Background thread polls GitHub API every 15 seconds (timeout: 10 minutes)
8. On completion, bot edits message to final state: "MORNING CHECK concluido" or "MORNING CHECK falhou" with buttons [Ver no GitHub] and [Voltar]

### Callback data prefixes

Following existing patterns (`tgl:`, `pg:`, `queue_page:`, `rpt_type:`):

- `wf_list` — show workflow list
- `wf_run:<workflow_id>` — trigger workflow
- `wf_cancel` — return to workflow list

### New module: `workflow_trigger.py`

Functions:
- `WORKFLOW_CATALOG` — static list of workflow definitions (id, name, description)
- `render_workflow_list(github_token)` — fetches last run for each workflow from GitHub API, returns formatted text + inline keyboard markup
- `trigger_workflow(workflow_id, github_token)` — dispatches workflow via GitHub API, returns success/error
- `poll_workflow_status(run_id, github_token, chat_id, message_id)` — runs in background thread, polls GitHub API every ~15s, edits Telegram message on state changes, stops on completion or 10min timeout
- `handle_wf_callback(callback_data, chat_id, message_id, callback_id)` — handles all `wf_*` callbacks

### GitHub API integration

Uses `requests` library (already a dependency) directly against GitHub REST API:
- `GET /repos/{owner}/{repo}/actions/runs` — list recent runs
- `POST /repos/{owner}/{repo}/actions/workflows/{id}/dispatches` — trigger run
- `GET /repos/{owner}/{repo}/actions/runs/{run_id}` — poll specific run status

Requires `GITHUB_TOKEN` env var in Railway (same token used by the dashboard).

### Status tracking

Background thread pattern matching existing `process_news_async` / `process_approval_async`:
- `threading.Thread(target=poll_workflow_status, args=(...), daemon=True).start()`
- Polls every 15 seconds
- Edits Telegram message via `edit_message` on state transitions
- Timeout at 10 minutes — edits message to "Timeout — verifique no GitHub"

### Integration points

- `/s` menu (`_show_main_menu` in app.py): add "Workflows" button that triggers `wf_list` callback
- `/workflows` command: added in the command handler section of `/webhook`
- `callback_router.py`: routes `wf_*` prefixes to `workflow_trigger.handle_wf_callback`
- `/admin/register-commands`: add `/workflows` to bot command menu

### Environment

Needs `GITHUB_TOKEN` added to Railway env vars (value: same GitHub personal access token used by the Vercel dashboard).

## Testing strategy

- **Refactor:** Run existing 288 tests after each module extraction to ensure no regressions
- **Workflow trigger:** Unit tests with mocked GitHub API responses for: list rendering, trigger dispatch, poll status transitions, timeout handling, callback routing
