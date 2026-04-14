# Workflow Progress Notifications — Design Spec

**Date:** 2026-04-13
**Status:** Approved design, pending implementation plan

## Goal

Send a single Telegram notification per GH Actions workflow run that is edited in place from start to finish, showing live progress ("preparing data" → "sending to N contacts (X/N)" → final summary). Replaces the current end-only summary.

## Motivation

Today each workflow sends one Telegram message at the end (via `DeliveryReporter._send_telegram_summary`). The user has no visibility while the workflow is running — a silent 5-minute gap between cron trigger and final report. Progress messages fix that without polluting the chat, by editing a single message throughout the run.

## Scope

**In scope — all 5 scheduled workflows:**

- `morning_check`
- `daily_report` (send_daily_report)
- `baltic_ingestion`
- `market_news` (send_news with market profile)
- `rationale_news` (send_news with rationale profile)

**Out of scope:**

- Webhook approval flow (already has its own editable-message UX for approvals)
- Persisting progress across workflow restarts (GH Actions runs are single-shot)
- Rendering progress in the dashboard (dashboard parses the stdout JSON block, unchanged)

## Architecture

Each workflow runs as a single Python process inside one GH Actions `run:` step. The `message_id` returned by Telegram's `sendMessage` is kept as an in-memory attribute of a new `ProgressReporter` instance for the duration of the process. No cross-step state passing is needed.

The new module wraps the existing `TelegramClient` (which already exposes `send_message` and `edit_message_text`) and integrates with the existing `DeliveryReporter` through its `on_progress` callback hook.

```
┌──────────────── Python process (GH Actions run step) ────────────────┐
│                                                                      │
│  progress = ProgressReporter(workflow=..., chat_id=...)              │
│  progress.start("Preparando dados...")  ─► Telegram sendMessage      │
│                                            (stores message_id)       │
│                                                                      │
│  [scrape / Claude / etc.]                                            │
│                                                                      │
│  progress.update("Enviando pra 100 contatos... (0/100)")             │
│                                            ─► editMessageText        │
│                                                                      │
│  reporter = DeliveryReporter(...,                                    │
│      notify_telegram=False,           # ← suppress old end-msg       │
│  )                                                                   │
│  reporter.dispatch(contacts, message,                                │
│      on_progress=progress.on_dispatch_tick)                          │
│                              │                                       │
│                              └── every ~10% or 5s: editMessageText   │
│                                                                      │
│  progress.finish(report)    ─► editMessageText (final summary using  │
│                                 existing _format_telegram_message)   │
└──────────────────────────────────────────────────────────────────────┘
```

## Components

### `execution/core/progress_reporter.py` (new)

Single class, ~150 lines.

```python
class ProgressReporter:
    def __init__(
        self,
        workflow: str,
        chat_id: Optional[str] = None,
        dashboard_base_url: str = "https://workflows-minerals.vercel.app",
        gh_run_id: Optional[str] = None,
        telegram_client: Optional[TelegramClient] = None,  # injectable for tests
    ): ...

    def start(self, phase_text: str = "Preparando dados...") -> None: ...
    def update(self, text: str) -> None: ...
    def on_dispatch_tick(
        self, processed: int, total: int, result: DeliveryResult
    ) -> None: ...
    def finish(self, report: DeliveryReport) -> None: ...
    def finish_empty(self, reason: str) -> None: ...
```

**Internal state:**

- `_message_id: Optional[int]` — set by `start()`, None if send failed
- `_last_edit_at: float` — `time.monotonic()` at last edit call
- `_last_edit_pct: int` — last integer percentage sent (to throttle by 10% steps)
- `_disabled: bool` — True when no chat_id and no default env, or when `start()` failed to obtain a `message_id`
- `_started_at: datetime` — captured on `start()` for duration display

**Behavior:**

- `start(phase_text)`: builds header `⏳ {WORKFLOW_UPPERCASE}\n{timestamp}\n{phase_text}`, calls `TelegramClient.send_message`, stores `message_id`. If send fails or returns None, marks `_disabled=True` and logs a warning. Never raises.
- `update(text)`: if disabled, no-op. Otherwise rebuilds the same header with the new `text` as the third line and calls `edit_message_text`. Failures log a warning and update `_last_edit_at` anyway (to avoid a busy-retry loop).
- `on_dispatch_tick(processed, total, result)`: computes `pct = int(processed / total * 100)`. Edits iff **any** of these is true: `(pct - _last_edit_pct) >= 10`, `(time.monotonic() - _last_edit_at) >= 5.0`, or `processed == total` (force final tick). On edit, text is `📤 Enviando pra {total} contatos... ({processed}/{total})`.
- `finish(report)`: edits the message with the full summary produced by the existing `_format_telegram_message(report, dashboard_base_url, gh_run_id)` from `delivery_reporter.py`. Same format used today — user preserves their existing look.
- `finish_empty(reason)`: edits to `ℹ️ {WORKFLOW_UPPERCASE}\n{timestamp}\n{reason}` for workflows that complete without an actual delivery (e.g., `market_news` when no new articles were found).

**Telegram parse mode:** Markdown (matches `_format_telegram_message` today, which emits markdown link at the end).

### `execution/core/delivery_reporter.py` (unchanged)

No code change required. `on_progress` already exists and accepts the right signature `(processed: int, total: int, result: DeliveryResult) -> None`. Scripts pass `progress.on_dispatch_tick` as `on_progress`.

The only behavioral change per-script is setting `notify_telegram=False` on the `DeliveryReporter` when a `ProgressReporter` is used — otherwise the user would get both the progressive edited message AND a new end-of-run message.

### Scripts — `execution/scripts/*.py` (modified)

Each of the 5 scripts gains a `ProgressReporter` wrapping its existing body. Pattern:

```python
# At top of script
progress = ProgressReporter(
    workflow="morning_check",
    chat_id=os.getenv("TELEGRAM_CHAT_ID"),
    gh_run_id=os.getenv("GITHUB_RUN_ID"),
)
progress.start("Preparando dados...")

try:
    # existing body (fetch platts, build message, ...)
    progress.update(f"Enviando pra {len(contacts)} contatos... (0/{len(contacts)})")
    reporter = DeliveryReporter(
        workflow="morning_check",
        send_fn=...,
        notify_telegram=False,            # suppress duplicate end msg
    )
    report = reporter.dispatch(
        contacts, message,
        on_progress=progress.on_dispatch_tick,
    )
    progress.finish(report)
except Exception as exc:
    progress.update(f"❌ Erro: {exc}")
    raise
```

For `market_news` / `rationale_news` with early-exit paths (no new articles), call `progress.finish_empty("sem items novos")` before returning.

## Data Flow & Cadence

**Cadence rule (C-hybrid from brainstorm):** edit iff `(pct - last_pct >= 10) OR (now - last_edit_at >= 5s) OR (processed == total)`.

Outcomes by list size (assuming send takes ~2s per contact):

| Contacts | Expected edits | Notes |
|---|---|---|
| 5 | 1 (final only) | Never hits 10% step with 5 items; 5×2s=10s barely crosses 5s once |
| 20 | ~4 | ~10% steps |
| 100 | ~10 | Pure 10% step cadence, ~1 edit per 20s |
| 500 | ~10 | Still 10% steps, ~1 edit per 100s |

Telegram rate limit for `editMessageText` is soft ~1/sec per chat; this cadence never approaches it.

## Error Handling

| Failure | Behavior |
|---|---|
| `start()` Telegram call fails | `_disabled=True`, log warning, workflow continues silently |
| `update()` Telegram call fails | Log warning, update `_last_edit_at`, continue. Next tick retries. |
| `finish()` Telegram call fails | Log warning. Message stays frozen at last tick. Dashboard link (via run_id env) still works. |
| `TELEGRAM_CHAT_ID` missing and no chat_id passed | `TelegramClient.default_chat_id` hardcoded fallback (`8375309778`) is used today. ProgressReporter follows the same rule; no special handling. |
| Workflow ends empty (no contacts to send) | Script calls `finish_empty(reason)` to replace placeholder with a final state. |
| `on_dispatch_tick` itself raises | Already swallowed by `DeliveryReporter.dispatch` (existing `try/except: pass` around progress callback). Resilient by construction. |

**Invariant:** No code path in `ProgressReporter` may raise. All failures degrade to log warnings.

## Testing

Tests live in `tests/test_progress_reporter.py`.

**Unit tests (mocked `TelegramClient`):**

1. `start()` stores `message_id` from `send_message` return value
2. `start()` returns None → reporter marked disabled, subsequent calls are no-ops
3. `update()` when disabled does not call TelegramClient
4. `on_dispatch_tick` emits first edit at 10% threshold, not before
5. `on_dispatch_tick` emits edit at 5s even when <10% progressed (simulated with monkeypatched `time.monotonic`)
6. `on_dispatch_tick` always emits when `processed == total`
7. Total edits for a 100-contact simulated dispatch ≤ 12 (10 pct steps + start + finish)
8. `finish()` calls `edit_message_text` with text containing ✅ for all-success report
9. `finish()` with >50% failure rate contains "FALHA TOTAL" (matches existing `_format_telegram_message` header logic)
10. `finish_empty(reason)` emits message with ℹ️ and the reason string
11. Any Telegram exception in `update()` is swallowed and logged
12. Any Telegram exception in `finish()` is swallowed and logged

**Integration test:**

13. Drive `ProgressReporter` through `start` → 100 simulated `on_dispatch_tick` calls → `finish`. Assert exact Telegram call sequence and that `finish` renders the `_format_telegram_message` output identically to what `DeliveryReporter` would have sent today (regression protection for the user-visible summary).

## Migration & Rollout

1. Land `ProgressReporter` + tests (no scripts touched). Tests pass.
2. Update `morning_check.py` only. Verify on next cron or manual trigger. Confirm chat still renders correctly, dashboard link still works.
3. Roll out to remaining 4 scripts in a single PR once #2 is validated.
4. No feature flag — the behavior change is visible (progressive edits vs. final-only send), and the user has explicitly approved it.

## Non-Goals

- No new external dependencies.
- No changes to `DeliveryReporter` behavior outside the existing `notify_telegram` toggle.
- No persistence, no retry queues, no backoff logic beyond the cadence throttle.
- No dashboard integration for progress — stdout JSON remains the dashboard's source of truth.
