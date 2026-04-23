# Testing Patterns

**Analysis Date:** 2026-04-22

Three test surfaces:

1. **Python / pytest** — for `execution/` and `webhook/` (53 test files, `tests/*.py`)
2. **JavaScript / vitest** — for two Apify actors (`platts-scrap-reports`, `platts-scrap-full-news`)
3. **None** — `dashboard/` (Next.js) and `webhook/mini-app/` have no test harness at all

This repo practices TDD discipline: every recent feature landed with a paired test module (queue bulk ops, split-lock idempotency, CallbackData factories, trace propagation). See `git log --oneline | grep '^test('` and the `docs/superpowers/plans/` structure.

## Test Framework

**Python:**
- Runner: `pytest` >= 7.0 (`requirements.txt`)
- Config: `/Users/bigode/Dev/agentics_workflows/pytest.ini`
  ```ini
  [pytest]
  testpaths = tests
  python_files = test_*.py
  python_classes = Test*
  python_functions = test_*
  addopts = -v --tb=short --ignore=tests/archive
  norecursedirs = .venv __pycache__ .git node_modules tests/archive
  ```
- Assertions: stdlib `assert`
- Plugins: `pytest-mock` >= 3.10 (the `mocker` fixture), `pytest-asyncio` >= 0.21, `fakeredis` >= 2.20

**JavaScript actors:**
- Runner: `vitest` ^2.1 (actor-local — `actors/platts-scrap-reports/package.json`, `actors/platts-scrap-full-news/package.json`)
- Assertions: `expect` from vitest
- Mocks: `vi.fn()`, `vi.spyOn(console, 'log')`, env manipulation via `process.env` snapshot/restore in `beforeEach`/`afterEach`
- Other two actors (`platts-news-only`, `platts-scrap-price`) have no tests — `"test"` script exits 1 with a TODO string

**Dashboard / mini-app:**
- No Jest / vitest / Playwright configured. `dashboard/package.json` only exposes `dev`, `build`, `start`, `lint`.

## Run Commands

```bash
# Python — full suite
pytest

# Python — single file, verbose
pytest tests/test_callbacks_queue.py -v

# Python — single test
pytest tests/test_state_store.py::test_record_success_writes_last_run_json

# Python — skip slow/archive (already excluded)
pytest --ignore=tests/archive

# Actor tests (per-actor)
cd actors/platts-scrap-reports && npm test          # vitest run
cd actors/platts-scrap-reports && npm run test:watch
```

## Test File Organization

**Location:**
- Python: all in repo-root `tests/` (flat — no subdirectories except `tests/archive/` which is git-kept but pytest-ignored)
- Actors: co-located under `actors/<actor>/tests/` alongside `actors/<actor>/src/`
- Dashboard / mini-app: none

**Naming:**
- Python: `test_<module-under-test>.py`. Test functions `test_<scenario>` with outcome phrase (`test_on_queue_page_happy_path_edits_message`, `test_on_queue_sel_toggle_preserves_current_page`)
- Actors: `<subject>.test.js`

**Archive:** `tests/archive/` holds retired tests kept for git history (e.g. `test_migrate_contacts_from_sheets.py` — post-Sheets migration). Excluded from every run via `pytest.ini` `--ignore` + `norecursedirs`.

## Full Python Test Inventory (`tests/*.py`, 53 files)

**Bot / aiogram handlers (router safety net):**
- `test_bot_callback_data.py` — pack/unpack roundtrip for every `CallbackData` factory (queue, curate, draft, report, contact, workflow, approval, subscription, onboarding, queue select-mode)
- `test_bot_delivery.py` — delivery reporter bot wrapper
- `test_bot_middlewares.py` — `RoleMiddleware` gating admin callbacks
- `test_bot_states.py` — FSM state class shapes
- `test_bot_users.py` — user registry / approval flow
- `test_callbacks_contacts.py` — contact page + toggle handlers
- `test_callbacks_curation.py` — `/queue` item curation (archive/reject/pipeline)
- `test_callbacks_menu.py` — `/menu` target routing
- `test_callbacks_queue.py` — queue navigation + select-mode + bulk prompt/confirm/cancel handlers (31 tests, most-recent)
- `test_callbacks_reports.py` — `/reports` navigation (type/year/month/download/back)
- `test_callbacks_workflows.py` — `/workflows` listing + run trigger
- `test_query_handlers.py` — `format_queue_page` rendering in normal + select modes
- `test_tail_command.py` — `/tail` command parsing + `parse_mode=None` rendering
- `test_reject_reason_flow.py` — draft reject reason FSM flow
- `test_messages_fsm_isolation.py` — per-user FSM state isolation

**Queue / curation / selection:**
- `test_queue_selection.py` — `webhook.queue_selection` set-mode state, atomic toggle, TTL, page persistence, per-chat isolation
- `test_curation_redis_client.py` — `execution.curation.redis_client` staging/archive/discard/seen/bulk_archive/bulk_discard (uses `fakeredis`)
- `test_curation_router.py` — curation routing logic
- `test_curation_telegram_poster.py` — `post_for_curation` message + keyboard
- `test_curation_id_gen.py` — stable id generation
- `test_rebuild_dedup.py` — dedup rebuild script
- `test_redis_queries.py` — `webhook.redis_queries.list_staging` paging

**State store / idempotency / event bus:**
- `test_state_store.py` — `record_success`, `record_failure` (streak increment), `record_empty`, `record_crash`, `_send_streak_alert` threshold
- `test_event_bus.py` — `EventBus` run_id gen, trace_id inheritance, stdout sink JSON, level coercion, supabase/telegram sink never-raise
- `test_morning_check_idempotency.py` — split-lock claim-ordering across every exit path
- `test_baltic_ingestion_idempotency.py` — same for `baltic_ingestion` script
- `test_dispatch_idempotency.py` — `webhook.dispatch` idempotent send
- `test_platts_ingestion_trace.py` — `trace_id` + `parent_run_id` injection into Apify `run_input`
- `test_platts_reports_trace.py` — same for reports actor

**Progress / delivery / reporting:**
- `test_agents_progress.py` — agents progress tracker
- `test_progress_reporter.py` — `ProgressReporter` text/sink
- `test_progress_reporter_sinks.py` — sink fan-out behaviour
- `test_delivery_reporter.py` — delivery summary rendering
- `test_digest.py` — `webhook.digest` compose logic
- `test_metrics_endpoint.py` — Prometheus `/metrics` route

**Integrations / repos:**
- `test_contacts_repo.py` — `ContactsRepo` CRUD against Supabase
- `test_contacts_repo_normalize.py` — phone / name normalization
- `test_contacts_bulk_ops.py` — bulk activate/deactivate
- `test_contact_admin.py` — admin contact management CLI
- `test_prompts.py` — prompt rendering (`execution/core/prompts/`)

**Cron / watchdog / workflow trigger:**
- `test_cron_parser.py` — crontab expression parser (croniter wrapper)
- `test_watchdog.py` — watchdog missed-cron detection
- `test_workflow_trigger.py` — GitHub Actions dispatch via octokit
- `test_webhook_status.py` — `/status` aggregation

**Mini-app (aiohttp + Jinja):**
- `test_mini_auth.py` — session / Telegram initData auth
- `test_mini_contacts.py` — contacts page
- `test_mini_news.py` — news page
- `test_mini_reports.py` — reports page
- `test_mini_stats.py` — stats page
- `test_mini_workflows.py` — workflows page

**Infrastructure:**
- `conftest.py` — shared fixtures (see below)
- `__init__.py` — package marker
- `_manual_format_check.py` — ad-hoc manual script, not a real test module (leading underscore bypasses `test_*` discovery)

## Shared Fixtures (`tests/conftest.py`)

**`sys.path` shim:** Inserts repo root and `webhook/` so tests can `import execution.*` and bare-import webhook modules (`redis_queries`, `query_handlers`) the way the Railway Docker container does.

**Bot fixtures** (used by every `test_callbacks_*.py`):
- `mock_bot` — `AsyncMock(spec=Bot)` with `send_message`, `edit_message_text`, `answer_callback_query`
- `mock_callback_query(user_id, chat_id, message_id, data)` — factory returning a `MagicMock(spec=CallbackQuery)` wired up with `from_user`, `message.chat`, `message.answer`, and an awaitable `answer()`
- `mock_message(text, chat_id, user_id)` — factory for `MagicMock(spec=Message)`
- `fsm_context_in_state(state, data)` — factory for `MagicMock(spec=FSMContext)` preloaded with `get_state` / `get_data` return values

## Mocking Patterns

**`fakeredis` for Redis-backed modules** — canonical pattern in `test_curation_redis_client.py`, `test_state_store.py`, `test_queue_selection.py`:
```python
@pytest.fixture
def fake_redis(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    from execution.curation import redis_client
    monkeypatch.setattr(redis_client, "_get_client", lambda: fake)
    return fake

@pytest.fixture(autouse=True)
def _reset_client_cache(monkeypatch):
    """Prevent cached client leaking between tests."""
    from execution.curation import redis_client
    monkeypatch.setattr(redis_client, "_client", None)
```

**Safety stubs in `state_store` fixture** (`tests/test_state_store.py:20-25`): the fixture also stubs `_send_streak_alert` to a no-op so a full run never leaks a real Telegram alert to the operator chat — regression guard for the 2026-04-21 incident where tests fired "TEST falhou 3x seguidas" into the admin chat.

**`mocker` (pytest-mock) for patching** — preferred over `monkeypatch` when patching module attributes in router tests:
```python
mocker.patch("webhook.queue_selection.is_select_mode", return_value=False)
mocker.patch("bot.routers.callbacks_queue.get_bot", return_value=bot)
mocker.patch("bot.routers.callbacks_queue.query_handlers.format_queue_page",
             return_value=("body", {"inline_keyboard": []}))
```

**`AsyncMock` for aiogram I/O** — `bot.edit_message_text`, `query.answer`, and `asyncio.to_thread` are always awaitable mocks:
```python
to_thread = mocker.patch("asyncio.to_thread", new=AsyncMock(return_value={"archived": ["a"], "failed": []}))
```

**Heavy-dep stubbing via `sys.modules`** (idempotency tests) — `pandas`/`spgci` aren't installed in CI, so `tests/test_morning_check_idempotency.py` injects `MagicMock` into `sys.modules` for those imports inside an autouse module-scope fixture, and removes them on teardown to avoid leaking stubs into other tests.

**Spy recorder pattern for call-ordering assertions** (`spy_state_store` fixture in `test_morning_check_idempotency.py:36-57`): build a dict of per-helper call-log lists, `monkeypatch.setattr` each helper to a closure that appends then returns a canned value. Tests then assert on the order of entries — the core idempotency invariant.

**Env isolation:** `monkeypatch.delenv(..., raising=False)` for `SUPABASE_URL`, `SUPABASE_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TRACE_ID`, `PARENT_RUN_ID` at the top of every `test_event_bus` test so defaults are deterministic.

**Actor tests (vitest):**
- `process.env` snapshot + restore in `beforeEach`/`afterEach`
- `vi.spyOn(console, 'log')` → parse `mock.calls[0][0]` as JSON to assert on emitted event fields
- Manual mock for `supabase.from(...).insert(...)` returning `Promise.resolve({})` or rejecting with an Error
- See `actors/platts-scrap-reports/tests/eventBus.test.js` for the canonical structure

## Idempotency Test Pattern (`test_*_idempotency.py`)

Every new cron with a 48h "already sent" guard ships with an idempotency test file that enforces the split-lock invariant. The pattern:

1. **Module-scope autouse fixture** stubs heavy deps (`pandas`, `spgci`) via `sys.modules`
2. **Per-test `spy_state_store` fixture** records call order of `check_sent_flag`, `try_claim_alert_key`, `set_sent_flag`, `release_inflight`
3. **`patched_integrations` fixture** replaces `PlattsClient`, `ContactsRepo`, `UazapiClient` with `MagicMock` instances configurable per scenario
4. **One test per exit path** — happy path, empty data, claim-lost (concurrent run), send failure, post-send crash
5. **Assert on `calls[...]` list ordering**, not just that helpers were called

Reference spec: `docs/superpowers/specs/2026-04-22-idempotency-claim-ordering-fix-design.md`.

## Test Categories

**Unit:** ~85% of the suite. Redis-backed modules (`fakeredis`), pure functions, FSM states, CallbackData roundtrip.

**Integration:** idempotency tests, trace-propagation tests, bot router tests with `asyncio.to_thread` + real format_queue_page patched at boundary. None hit live Redis / Supabase / Telegram; everything is stubbed.

**E2E:** none. No Playwright / Cypress / manual scripts beyond `tests/_manual_format_check.py`. The dashboard and Telegram mini-app have zero automated E2E coverage.

## Coverage

- No coverage tool (`coverage.py` / `pytest-cov`) currently configured — target is 80% per user global rules but not measured in CI
- To measure ad-hoc: `pip install pytest-cov && pytest --cov=execution --cov=webhook --cov-report=term-missing`
- Observed gaps: dashboard (0%), webhook mini-app HTML rendering (tested at handler level only), two actors without test files (`platts-news-only`, `platts-scrap-price`)

## CI Test Execution

**GitHub Actions workflows (`.github/workflows/`):**
- `baltic_ingestion.yml`, `daily_report.yml`, `market_news.yml`, `morning_check.yml`, `platts_reports.yml`, `watchdog.yml`

**None of these run `pytest`** — they invoke `execution/scripts/<workflow>.py` directly as production crons. There is currently **no CI job** that runs the test suite on push / PR. Tests run locally or via `pytest` manually.

**Actor tests** also run only locally (`npm test` inside the actor directory) — no GitHub Actions job invokes vitest.

## Actor Test Pattern

`actors/platts-scrap-reports/tests/` contains four vitest files:
- `eventBus.test.js` — `EventBus` constructor validation, runId generation, traceId inheritance, stdout JSON shape, Supabase sink fan-out, never-raise guarantees, circular-ref detail handling
- `filters.test.js` — report filter logic
- `dates.test.js` — date parsing
- `slug.test.js` — URL slug generation

`actors/platts-scrap-full-news/tests/eventBus.test.js` mirrors the reports eventBus test (the two actors keep `EventBus` in sync).

ESM, `import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'`, no global setup file — each spec isolates its own env and mocks.

## TDD Evidence (2026-04-22 queue bulk ops)

Recent commits show strict TDD cadence for the `/queue` select-mode + bulk ops feature:
- `27c268f docs(spec): /queue bulk archive+discard design`
- `5c2329b docs(plan): /queue bulk actions implementation — 7 tasks`
- `e9901c3 feat(queue): add queue_selection state module for bulk actions` (shipped with `test_queue_selection.py`)
- `3c3332c refactor(queue_selection): atomic toggle + transactional pipelines`
- `183e476 feat(curation): add bulk_archive and bulk_discard helpers` (shipped with `test_curation_redis_client.py` coverage)
- `d873fa9 feat(bot): add 7 CallbackData classes for /queue select mode` (shipped with `test_bot_callback_data.py` roundtrip)
- `f2897d9 / fa4b042 / 7e8f29f feat(queue): handlers ...` (each with `test_callbacks_queue.py` cases)
- `f940166 fix(queue): singular/plural in select-mode header, order-sensitive + factory-parity tests`
- `7d5f337 refactor(queue): bulk-op error recovery + singular/plural polish`
- `38f59f3 fix(queue): preserve current page when toggling selection` (regression test `test_on_queue_sel_toggle_preserves_current_page`)

Split-lock idempotency (commits `d4f3592`, `a13f9b7`, `43aa332`, `35629d4`) shipped with `test_baltic_ingestion_idempotency.py` + `test_morning_check_idempotency.py` regression guards.

## Known Gaps & Flaky-Test Handling

- **Dashboard coverage: 0%.** No Jest config, no Playwright. High-priority gap since the dashboard renders production data.
- **Mini-app HTML templates:** handler tests (`test_mini_*.py`) assert on route logic, not the Jinja output.
- **Two actors have no tests:** `platts-news-only`, `platts-scrap-price` (`"test"` script exits 1 with a TODO string).
- **No flake retry config.** Tests are expected deterministic; `fakeredis` + `monkeypatch.delenv` patterns isolate env/state. The `_send_streak_alert` stub in `test_state_store.py` was added after a real-world leak — treat every Telegram/Supabase/HTTP boundary as a must-stub surface.
- **No CI enforcement** — contributors must run `pytest` locally before pushing.

---

*Testing analysis: 2026-04-22*
