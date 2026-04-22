# Observability Phase 2 — Events Channel Followups

**Shipped:** 2026-04-21 on branch `feature/observability-phase2` (commits `ee584d6..f38a19d`)
**Spec:** `docs/superpowers/specs/2026-04-21-observability-unified-design.md` §§220-244
**Plan:** `docs/superpowers/plans/2026-04-21-observability-phase2-events-channel-plan.md`

## Commits on the branch

| SHA | Task | Summary |
|---|---|---|
| `ee584d6` | 1 | `_EventsChannelSink` class + `_build_sinks` wiring + 5 tests |
| `f38a19d` | 2 | Add `TELEGRAM_EVENTS_CHANNEL_ID` to 6 workflow YAMLs |

**Test count:** 497 → **502 passed** (+5 new), 3 pre-existing failed (unchanged).

---

## Operator validation

- [x] New Telegram group created, bot added as admin, numeric ID captured — operator done before plan started.
- [x] `TELEGRAM_EVENTS_CHANNEL_ID` set in GH repo secrets.
- [ ] Merge this branch to main and push — the next scheduled or manually-triggered workflow starts relaying.
- [ ] Trigger any workflow via GH Actions UI (`morning_check` → "Run workflow" is quickest).
- [ ] Events channel receives a compact message within ~1-2s of `cron_started` — format: `HH:MM:SS ℹ️ morning_check.cron_started`.
- [ ] Run completes → events channel receives `cron_finished` (possibly batched with any intermediate events, as one multi-line message).
- [ ] Deliberate crash test (throwaway branch): events channel receives `🚨 morning_check.cron_crashed — <exc>:<msg>` IMMEDIATELY (not batched — error path flushes synchronously).
- [ ] Noise check over 2-3 days: confirm the message volume is manageable. If too loud, options:
  - Tighten the level gate (e.g., only `warn`/`error` go to events channel — effectively making it a backup of main chat).
  - Lower `_MAX_BUFFER` from 20 to 10 to batch smaller (more messages, less per-message).
  - Raise `_BATCH_WINDOW_SECONDS` from 1.0 to 2.0 to batch more aggressively.

---

## Known followups (out of Phase 2 scope)

### A. Resilience

1. **`atexit` doesn't fire on hard kill.** If a GH Actions run is cancelled (user presses cancel, or cron grace expires), any pending info events in the buffer are lost. Acceptable — error/warn events are NEVER buffered so they always land.

2. **No retry on Telegram 429.** If the events channel hits Telegram's rate limit, `send_message` raises, we log a warning, and the next window tries fresh. Consider adding exponential backoff + a small in-memory retry queue in Phase 4 if we see sustained 429s.

3. **Client leak on long-lived processes.** `_EventsChannelSink` instantiates a `TelegramClient` once at `__init__`, stores it. For a short-lived GH Actions run this is correct. For a hypothetical long-running process that creates many `EventBus` instances (doesn't exist today), clients would accumulate. Not a concern unless/until such a process exists.

### B. Formatting

4. **`parse_mode=None` means raw text — no clickable links.** If operators want `[dashboard](url)` in events-channel messages, switch the footer to `parse_mode="Markdown"` and escape user-provided content (`_`, `*`, `[`, `]`) in labels. One-line change in `_send_one`. Out of Phase 2 scope.

5. **Batched message layout is plain text with emojis.** Readable, but cramped at 20 events. Alternatives if operators complain:
   - Lower `_MAX_BUFFER` to 10 (smaller messages, more frequent).
   - Group by workflow inside `_format` (e.g., `### morning_check\n<events>\n### daily_report\n<events>`).
   - Add 1 blank line between events. Trivial change.

6. **Label truncated at 80 chars.** If exception messages or step labels carry useful context past 80 chars, operator will need to `/tail` (Phase 4) to see the full detail. Acceptable tradeoff for message brevity.

### C. De-duplication

7. **Events channel alerts have no dedup with main chat.** A `cron_crashed` goes to BOTH chats. Intentional belt-and-suspenders — the whole point of the events channel is completeness. If noise becomes an issue, `_EventsChannelSink._should_relay` could skip events where `_MainChatSink` also fires. Weigh against the loss of "complete audit trail" property.

### D. Deployment

8. **Railway does not get `TELEGRAM_EVENTS_CHANNEL_ID`.** The bot on Railway (webhook/) does not create `EventBus` instances today — confirmed via grep. If Phase 4 adds event emissions from bot handlers (e.g., `cmd_tail` calls), Railway will need the env var. Safe to add preemptively when Phase 4 lands.

9. **`platts_ingestion.py` has `@with_event_bus` but no `.yml` in `.github/workflows/`.** If it runs (via being called from another script, or on demand), it also needs this env var to reach the channel. If it's truly unused, consider removing it. Pre-existing drift, not caused by Phase 2.

### E. Testing gaps

10. **No test for `atexit` flush path.** The handler is registered but the test harness doesn't actually shut down Python between tests, so the `_flush_on_exit` method is untested. Could be covered with a test that manually calls `sink._flush_on_exit()` and asserts the buffer was drained. Low priority — the code is simple enough to trust by inspection.

11. **No test for Telegram send failure.** `_send_one` swallows exceptions from `send_message`. Could add a test with a `FakeTelegramClient` that raises, assert `logger.warning` was called, assert other sinks still fire. Low priority — same try/except pattern as other sinks already covered.

---

## Phase 4 prerequisites

Nothing extra for Phase 4 (`/tail` + `step`/`api_call` instrumentation) to proceed. The events channel is a read-downstream sink; Phase 4 is about write-upstream instrumentation inside scripts. When Phase 4 lands, the events channel will automatically start relaying `step` and `api_call` events without any changes here.

---

## Open questions resolved during implementation

- **Does `atexit.register` work correctly for per-instance handlers?** Yes — each `EventBus`/`_EventsChannelSink` instance registers its own `_flush_on_exit`. Python's atexit holds references; GC doesn't collect the sink before shutdown.
- **Does `parse_mode=None` work with the existing `TelegramClient.send_message` signature?** Yes — `def send_message(self, text, chat_id=None, reply_markup=None, parse_mode="Markdown")`. Passing `parse_mode=None` disables parsing; text is delivered verbatim.
- **Does the 1-second window use wall clock or monotonic time?** Monotonic (via `time.monotonic()`). Immune to system clock changes (NTP sync, DST) during a run.
- **Buffer overflow policy?** Flush at 20 events is hard-stop — after that, every subsequent info event immediately flushes a 1-event message (or groups with the next since buffer is empty again). No silent drops.
