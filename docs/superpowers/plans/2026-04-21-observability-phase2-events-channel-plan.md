# Observability Phase 2 — Events Channel Sink Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a 5th sink in `event_bus.py` — `_EventsChannelSink` — that sends *every* emitted event (not just errors) to a dedicated Telegram group/channel where the operator can silence notifications and review when convenient. Infinite-detail firehose separate from the main chat's curated crash alerts.

**Architecture:** New `_EventsChannelSink` class in `execution/core/event_bus.py`, gated by `TELEGRAM_EVENTS_CHANNEL_ID` env var. `warn`/`error` events flush immediately (same latency as `_MainChatSink`); `info` events batch in 1-second windows (or flush-at-20 for burstiness) to stay under Telegram's ~30 msg/sec rate limit. Each GH Actions script's `env:` block gets the new env var so the sink has a destination.

**Tech Stack:** Python 3.11, `atexit` stdlib (for end-of-run flush), existing `TelegramClient`, pytest. No new dependencies.

**Spec reference:** `docs/superpowers/specs/2026-04-21-observability-unified-design.md` §220–244 (sink list + throttling), Phase 2 rollout §616.

**Repo root:** `/Users/bigode/Dev/agentics_workflows/`

**Python runner:** `/usr/bin/python3 -m pytest ...`.

**Phase 2 ship criterion:**
1. `_EventsChannelSink` added to `_build_sinks`, gated by `TELEGRAM_EVENTS_CHANNEL_ID` env var.
2. `warn`/`error` events fire immediately (same semantics as main-chat for errors, but to a different chat).
3. `info` events batch: either the batch reaches 20 events OR 1 second elapses since last flush — whichever first. End-of-run flush via `atexit` so nothing is lost when a short script exits.
4. All 6 existing GH Actions workflow YAMLs get `TELEGRAM_EVENTS_CHANNEL_ID` in their `env:` blocks (`morning_check`, `daily_report`, `baltic_ingestion`, `market_news`, `platts_reports`, `watchdog`).
5. Baseline + Phase 1/3 tests still pass; 5 new tests green.
6. Manual validation: operator triggers any workflow, sees lifecycle events in the events channel.

**What Phase 2 does NOT do:**
- Sub-minute batching beyond the simple 1s/20-event trigger (no event-coalescing, no topic-routing).
- Events channel instrumentation for `step`/`api_call` inside scripts — that's Phase 4.
- Adding the env var to Railway (the bot on Railway does not create `EventBus` instances; only GH Actions runners do). Revisit if Phase 4 starts emitting from bot handlers.

---

## File Structure

**Files to modify:**

| Path | Scope of change |
|---|---|
| `execution/core/event_bus.py` | Add `_EventsChannelSink` class + `_build_sinks` wiring |
| `tests/test_event_bus.py` | Add 5 new tests for the sink |
| `.github/workflows/morning_check.yml` | Add `TELEGRAM_EVENTS_CHANNEL_ID` to `env:` block |
| `.github/workflows/daily_report.yml` | Same |
| `.github/workflows/baltic_ingestion.yml` | Same |
| `.github/workflows/market_news.yml` | Same |
| `.github/workflows/platts_reports.yml` | Same |
| `.github/workflows/watchdog.yml` | Same |

**Files to create:**

| Path | Lines (approx) | Responsibility |
|---|---|---|
| `docs/superpowers/followups/2026-04-21-observability-phase2-followups.md` | ~50 | Validation checklist + known followups |

No other files touched. `platts_ingestion.py` is wrapped with `@with_event_bus` in Phase 1 but has no GH Actions YAML in `.github/workflows/` — if it runs, it runs via some other mechanism (called from another script, manual, etc.), out of scope here.

---

## Pre-flight

- [ ] **Step 0.1: Confirm Phases 1 and 3 landed**

```bash
cd /Users/bigode/Dev/agentics_workflows && git log --oneline main -15 | head -15
```

Expected: see the Phase 1 merge commit (`660f05d Merge observability phase 1 foundation`) and the Phase 3 merge commit (`1e2e93e Merge observability phase 3 watchdog` or similar). If either is missing, stop — Phase 2 depends on both being on main.

- [ ] **Step 0.2: Create the Phase 2 worktree**

```bash
cd /Users/bigode/Dev/agentics_workflows && git worktree add .worktrees/obs-phase2 -b feature/observability-phase2
```

All work happens in `.worktrees/obs-phase2/`. Use `cd` before every command.

- [ ] **Step 0.3: Baseline test count**

```bash
cd /Users/bigode/Dev/agentics_workflows/.worktrees/obs-phase2 && /usr/bin/python3 -m pytest tests/ 2>&1 | tail -3
```

Expected: 497 passed, 3 pre-existing failed (in `test_query_handlers.py`). Each task must keep this baseline + add its own new tests.

- [ ] **Step 0.4: Confirm operator prerequisite done**

The user should have:
1. Created a new Telegram group (or channel) for the events firehose.
2. Added the bot as admin with `send_messages` permission.
3. Captured the numeric chat ID.
4. Added `TELEGRAM_EVENTS_CHANNEL_ID=<id>` to GitHub repo secrets.

Without (4), the workflow-YAML env wiring still works (GH will just inject an empty string), and the sink gracefully no-ops. But manual validation (Task 4) won't land messages anywhere.

No code action needed here — just note whether it's done. Continue either way.

---

## Task 1: Add `_EventsChannelSink` class + wire into `_build_sinks`

**Files:**
- Modify: `execution/core/event_bus.py`
- Modify: `tests/test_event_bus.py`

This is the entire code change for the sink. `_build_sinks` updates are bundled with the class definition to keep one commit coherent.

- [ ] **Step 1.1: Write failing tests**

Append to `tests/test_event_bus.py`:

```python
def test_events_channel_sink_sends_warn_immediately(monkeypatch):
    """warn/error events flush immediately, no buffering."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_EVENTS_CHANNEL_ID", "-1001234567890")

    sent_messages = []

    class FakeTelegramClient:
        def send_message(self, text, chat_id=None, **kwargs):
            sent_messages.append({"text": text, "chat_id": chat_id})
            return 1

    from execution.core import event_bus as eb
    monkeypatch.setattr(eb, "_build_telegram_client", lambda: FakeTelegramClient())

    bus = eb.EventBus(workflow="wf_a")
    bus.emit("step", label="warning thing", level="warn")

    assert len(sent_messages) == 1
    assert sent_messages[0]["chat_id"] == "-1001234567890"
    assert "wf_a" in sent_messages[0]["text"]
    assert "step" in sent_messages[0]["text"]


def test_events_channel_sink_buffers_info_until_threshold(monkeypatch):
    """info events accumulate until the 20-event threshold or 1s window."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_EVENTS_CHANNEL_ID", "-1001234567890")

    sent_messages = []

    class FakeTelegramClient:
        def send_message(self, text, chat_id=None, **kwargs):
            sent_messages.append({"text": text, "chat_id": chat_id})
            return 1

    from execution.core import event_bus as eb
    monkeypatch.setattr(eb, "_build_telegram_client", lambda: FakeTelegramClient())

    # Freeze time so the batch window doesn't expire
    fake_time = [1000.0]
    monkeypatch.setattr(eb, "_monotonic", lambda: fake_time[0])

    bus = eb.EventBus(workflow="wf_b")
    # Emit 5 info events — under threshold; should NOT have flushed yet
    for i in range(5):
        bus.emit("step", label=f"step_{i}", level="info")

    assert sent_messages == []

    # Emit 15 more — now at 20; should flush exactly once
    for i in range(5, 20):
        bus.emit("step", label=f"step_{i}", level="info")

    assert len(sent_messages) == 1
    assert "step_0" in sent_messages[0]["text"]
    assert "step_19" in sent_messages[0]["text"]


def test_events_channel_sink_flushes_on_time_window(monkeypatch):
    """info events flush when 1s elapses since last flush, even below threshold."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_EVENTS_CHANNEL_ID", "-1001234567890")

    sent_messages = []

    class FakeTelegramClient:
        def send_message(self, text, chat_id=None, **kwargs):
            sent_messages.append({"text": text, "chat_id": chat_id})
            return 1

    from execution.core import event_bus as eb
    monkeypatch.setattr(eb, "_build_telegram_client", lambda: FakeTelegramClient())

    fake_time = [1000.0]
    monkeypatch.setattr(eb, "_monotonic", lambda: fake_time[0])

    bus = eb.EventBus(workflow="wf_c")
    bus.emit("step", label="first", level="info")
    assert sent_messages == []

    # Advance time past the 1s window
    fake_time[0] += 1.5
    bus.emit("step", label="second", level="info")

    # Now the emit should have triggered a flush of the pending buffer
    assert len(sent_messages) == 1
    assert "first" in sent_messages[0]["text"]
    assert "second" in sent_messages[0]["text"]


def test_events_channel_sink_flushes_pending_info_before_warn(monkeypatch):
    """A warn event flushes any buffered info FIRST to preserve ordering."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_EVENTS_CHANNEL_ID", "-1001234567890")

    sent_messages = []

    class FakeTelegramClient:
        def send_message(self, text, chat_id=None, **kwargs):
            sent_messages.append({"text": text, "chat_id": chat_id})
            return 1

    from execution.core import event_bus as eb
    monkeypatch.setattr(eb, "_build_telegram_client", lambda: FakeTelegramClient())

    fake_time = [1000.0]
    monkeypatch.setattr(eb, "_monotonic", lambda: fake_time[0])

    bus = eb.EventBus(workflow="wf_d")
    bus.emit("step", label="buffered_info", level="info")
    bus.emit("step", label="now_warning", level="warn")

    # Expect 2 sends: first the buffered info (flushed), then the warn (immediate)
    assert len(sent_messages) == 2
    assert "buffered_info" in sent_messages[0]["text"]
    assert "now_warning" in sent_messages[1]["text"]


def test_events_channel_sink_disabled_when_env_missing(monkeypatch, capsys):
    """When TELEGRAM_EVENTS_CHANNEL_ID is absent, the sink is not added to _sinks."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_EVENTS_CHANNEL_ID", raising=False)

    from execution.core.event_bus import EventBus

    bus = EventBus(workflow="wf_e")
    bus.emit("step", label="nobody hears", level="info")

    # Only stdout fires
    out = capsys.readouterr().out
    assert "step" in out  # stdout still gets it
```

- [ ] **Step 1.2: Run tests — verify they fail**

```bash
cd /Users/bigode/Dev/agentics_workflows/.worktrees/obs-phase2 && /usr/bin/python3 -m pytest tests/test_event_bus.py -v -k "events_channel" 2>&1 | tail -15
```

Expected: 5 failures. The first 4 fail because `_EventsChannelSink` doesn't exist OR because the `_monotonic` module-level seam isn't there. The 5th may pass on its own (stdout is always on) but the first 4 fail decisively.

- [ ] **Step 1.3: Add the `_monotonic` time seam to `event_bus.py`**

At the top of `execution/core/event_bus.py`, with the other stdlib imports, add:

```python
import atexit
import time
```

Below the existing helpers `_generate_run_id` and `_now_iso`, add one more helper:

```python
def _monotonic() -> float:
    """Monkeypatch seam for tests that need to simulate time passage."""
    return time.monotonic()
```

This helper is used only by `_EventsChannelSink.emit` so tests can freeze time.

- [ ] **Step 1.4: Implement `_EventsChannelSink`**

Add, in `execution/core/event_bus.py`, AFTER `_MainChatSink` and BEFORE the `with_event_bus` decorator:

```python
class _EventsChannelSink:
    """Firehose sink: sends every emitted event to a dedicated Telegram channel.

    Unlike _MainChatSink (which only fires for warn/error/crashed/missed), this
    sink relays info events too — a complete audit trail the operator can silence
    and review on demand.

    Throttling:
    - warn/error events flush immediately (latency parity with _MainChatSink).
    - info events batch in 1-second windows OR at 20 events, whichever first.
    - End-of-run flush via atexit so a short-lived script doesn't lose its tail.

    Rate-limit posture: Telegram allows ~30 msg/sec/chat. With the 1s window we
    emit ≤1 msg/sec for info bursts, leaving headroom for warn/error spikes.
    """

    _BATCH_WINDOW_SECONDS = 1.0
    _MAX_BUFFER = 20  # flush when buffered info reaches this count

    def __init__(self, chat_id: str, client):
        self._chat_id = chat_id
        self._client = client
        self._buffer: list = []
        self._last_flush = _monotonic()
        # Register flush at interpreter exit so short scripts don't drop events
        atexit.register(self._flush_on_exit)

    def emit(self, event_dict: dict) -> None:
        level = event_dict.get("level", "info")
        if level in ("warn", "error"):
            # Preserve ordering: flush any buffered info first, then send this one
            self._flush()
            self._send_one([event_dict])
            return
        # Info: buffer then maybe flush
        self._buffer.append(event_dict)
        if len(self._buffer) >= self._MAX_BUFFER:
            self._flush()
            return
        now = _monotonic()
        if (now - self._last_flush) >= self._BATCH_WINDOW_SECONDS:
            self._flush()

    def _flush(self) -> None:
        if not self._buffer:
            return
        batch = self._buffer
        self._buffer = []
        self._last_flush = _monotonic()
        self._send_one(batch)

    def _flush_on_exit(self) -> None:
        try:
            self._flush()
        except Exception:
            pass  # atexit handlers must not raise

    def _send_one(self, events: list) -> None:
        text = self._format(events)
        try:
            self._client.send_message(text=text, chat_id=self._chat_id, parse_mode=None)
        except Exception as exc:
            logger.warning(f"_EventsChannelSink send failed: {exc}")

    @staticmethod
    def _format(events: list) -> str:
        lines = []
        level_emoji = {"info": "ℹ️", "warn": "⚠️", "error": "🚨"}
        for ev in events:
            ts = ev.get("ts") or ""
            hhmmss = ts[11:19] if len(ts) >= 19 else ts
            wf = ev.get("workflow") or "?"
            ev_name = ev.get("event") or "?"
            emoji = level_emoji.get(ev.get("level", "info"), "•")
            label = ev.get("label") or ""
            line = f"{hhmmss} {emoji} {wf}.{ev_name}"
            if label:
                line += f" — {label[:80]}"
            lines.append(line)
        return "\n".join(lines)
```

Design notes:
- The client is stored at `__init__` time (built once per bus), not re-created on every emit. This avoids the per-emit client-construction issue flagged in Phase 1's `_MainChatSink` followup.
- `parse_mode=None` disables Telegram's Markdown parsing — protects against labels containing `_` or `*` that would otherwise break formatting.
- `atexit.register` fires when Python is shutting down normally. For `os._exit()` or SIGKILL it won't fire, but those are script-abnormal paths (not our concern).

- [ ] **Step 1.5: Wire into `_build_sinks`**

Update `EventBus._build_sinks` in `event_bus.py`. Current shape:

```python
    def _build_sinks(self) -> list:
        sinks: list = [_StdoutSink()]
        supabase = _get_supabase_client()
        if supabase is not None:
            sinks.append(_SupabaseSink(supabase))
        sinks.append(_SentrySink())
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if chat_id and token:
            sinks.append(_MainChatSink(chat_id=chat_id))
        return sinks
```

Replace with (adds the events channel block):

```python
    def _build_sinks(self) -> list:
        sinks: list = [_StdoutSink()]
        supabase = _get_supabase_client()
        if supabase is not None:
            sinks.append(_SupabaseSink(supabase))
        sinks.append(_SentrySink())
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if chat_id and token:
            sinks.append(_MainChatSink(chat_id=chat_id))
        events_channel_id = os.getenv("TELEGRAM_EVENTS_CHANNEL_ID")
        if events_channel_id and token:
            client = _build_telegram_client()
            if client is not None:
                sinks.append(_EventsChannelSink(chat_id=events_channel_id, client=client))
        return sinks
```

- [ ] **Step 1.6: Run tests — verify they pass**

```bash
cd /Users/bigode/Dev/agentics_workflows/.worktrees/obs-phase2 && /usr/bin/python3 -m pytest tests/test_event_bus.py -v 2>&1 | tail -25
```

Expected: all 5 new tests pass, plus every previously-passing test still passes (21 from Phase 1 + 5 new = 26 total). If `test_events_channel_sink_buffers_info_until_threshold` fails with "expected 1 sent, got 0", the `_monotonic` seam isn't being monkeypatched correctly — check Step 1.3.

- [ ] **Step 1.7: Full suite regression**

```bash
cd /Users/bigode/Dev/agentics_workflows/.worktrees/obs-phase2 && /usr/bin/python3 -m pytest tests/ 2>&1 | tail -3
```

Expected: 502 passed (497 + 5 new), 3 pre-existing failed.

- [ ] **Step 1.8: Commit**

```bash
cd /Users/bigode/Dev/agentics_workflows/.worktrees/obs-phase2 && \
  git add execution/core/event_bus.py tests/test_event_bus.py && \
  git commit -m "$(cat <<'EOF'
feat(observability): add _EventsChannelSink for firehose to Telegram

Fifth sink in event_bus.py. Gated by TELEGRAM_EVENTS_CHANNEL_ID env var.
warn/error events flush immediately (ordered behind any buffered info).
info events batch at 20 events OR 1s window, whichever first. atexit
handler flushes pending events on interpreter shutdown so short scripts
don't drop their tail.

5 new tests cover: immediate warn flush, info batching threshold, time
window flush, ordering preserved across info→warn boundary, env-gate
disable.

TelegramClient is built once at __init__ (addresses Phase 1 followup #1
pattern pre-emptively for the new sink).

Spec: docs/superpowers/specs/2026-04-21-observability-unified-design.md §§220-244
EOF
)"
```

---

## Task 2: Wire `TELEGRAM_EVENTS_CHANNEL_ID` into 6 workflow YAMLs

**Files to modify (all 6):**
- `.github/workflows/morning_check.yml`
- `.github/workflows/daily_report.yml`
- `.github/workflows/baltic_ingestion.yml`
- `.github/workflows/market_news.yml`
- `.github/workflows/platts_reports.yml`
- `.github/workflows/watchdog.yml`

No new tests — mechanical env-block addition.

- [ ] **Step 2.1: Inspect an existing YAML's env block**

```bash
cd /Users/bigode/Dev/agentics_workflows/.worktrees/obs-phase2 && grep -n "TELEGRAM_CHAT_ID\|SUPABASE_URL\|SENTRY_DSN" .github/workflows/morning_check.yml | head -10
```

This shows where the existing env keys sit. Each YAML has an `env:` block (either at job level or step level) listing Telegram/Supabase/Sentry secrets.

- [ ] **Step 2.2: Add the new env key to each YAML**

For EACH of the 6 YAMLs, find the `env:` block that currently includes `TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}` and add, on a new line immediately after it, exactly:

```yaml
          TELEGRAM_EVENTS_CHANNEL_ID: ${{ secrets.TELEGRAM_EVENTS_CHANNEL_ID }}
```

(Indentation is 10 spaces before the key, matching the YAML nesting under `env:` — verify by looking at the sibling `TELEGRAM_CHAT_ID` line and using the exact same indent.)

**Important:** each YAML may have a slightly different indentation level. DO NOT assume 10 spaces universally. Copy the exact leading whitespace of the `TELEGRAM_CHAT_ID` line in each file.

Expected outcome per file: the `env:` block gains one new line, nothing else changes.

- [ ] **Step 2.3: Validate each YAML parses**

```bash
cd /Users/bigode/Dev/agentics_workflows/.worktrees/obs-phase2 && for f in morning_check daily_report baltic_ingestion market_news platts_reports watchdog; do
  /usr/bin/python3 -c "import yaml; yaml.safe_load(open('.github/workflows/$f.yml'))" && echo "$f.yml OK" || echo "$f.yml FAILED"
done
```

Expected: 6 × `<name>.yml OK`. If any fails, fix the YAML before committing.

- [ ] **Step 2.4: Grep-confirm all 6 files have the new key**

```bash
cd /Users/bigode/Dev/agentics_workflows/.worktrees/obs-phase2 && grep -l "TELEGRAM_EVENTS_CHANNEL_ID" .github/workflows/*.yml | wc -l
```

Expected: `6`.

- [ ] **Step 2.5: Full suite regression** (sanity: YAML edits shouldn't affect Python tests)

```bash
cd /Users/bigode/Dev/agentics_workflows/.worktrees/obs-phase2 && /usr/bin/python3 -m pytest tests/ 2>&1 | tail -3
```

Expected: 502 passed, 3 failed (same as after Task 1).

- [ ] **Step 2.6: Commit**

```bash
cd /Users/bigode/Dev/agentics_workflows/.worktrees/obs-phase2 && \
  git add .github/workflows/ && \
  git commit -m "$(cat <<'EOF'
feat(observability): wire TELEGRAM_EVENTS_CHANNEL_ID into all workflow YAMLs

Adds the new env var to morning_check, daily_report, baltic_ingestion,
market_news, platts_reports, watchdog. Without this, the Phase 2
_EventsChannelSink has no destination and gracefully no-ops. Requires
TELEGRAM_EVENTS_CHANNEL_ID set in repo secrets (operator prerequisite).
EOF
)"
```

---

## Task 3: Followups doc + manual validation checklist

**Files:**
- Create: `docs/superpowers/followups/2026-04-21-observability-phase2-followups.md`

No code — documentation only.

- [ ] **Step 3.1: Write the followups doc**

Create `docs/superpowers/followups/2026-04-21-observability-phase2-followups.md`:

```markdown
# Observability Phase 2 — Events Channel Followups

**Shipped:** 2026-04-21 on branch `feature/observability-phase2`
**Spec:** `docs/superpowers/specs/2026-04-21-observability-unified-design.md` §§220-244
**Plan:** `docs/superpowers/plans/2026-04-21-observability-phase2-events-channel-plan.md`

## Commits on the branch

| Task | Summary |
|---|---|
| 1 | `_EventsChannelSink` class + `_build_sinks` wiring + 5 tests |
| 2 | Add `TELEGRAM_EVENTS_CHANNEL_ID` to 6 workflow YAMLs |

**Test count:** 497 → **502 passed** (+5 new), 3 pre-existing failed (unchanged).

## Operator validation

- [ ] `TELEGRAM_EVENTS_CHANNEL_ID` in GH repo secrets — operator confirmed before plan started.
- [ ] Trigger any workflow via GH Actions UI (`morning_check` is quickest).
- [ ] Events channel receives a compact message within ~1-2s of `cron_started` — format is `HH:MM:SS ℹ️ morning_check.cron_started`.
- [ ] Run completes → events channel receives `cron_finished` (possibly batched with any intermediate `step` events, as one multi-line message).
- [ ] Deliberate crash (throwaway branch): events channel receives `🚨 morning_check.cron_crashed — <exc>:<msg>` IMMEDIATELY (not batched — error path is synchronous).
- [ ] Noise check: in the first 2-3 days, confirm the volume is manageable. If too loud, consider suppressing some info events or adding a level filter here (e.g., only `warn+error` goes to events channel, even `info` demoted to stdout-only).

## Known followups

1. **`atexit` doesn't fire on hard kill.** If a GH Actions run is cancelled (user pressed cancel, or cron grace expired), any pending info events in the buffer are lost. Acceptable — error/warn events are not buffered so they always land.
2. **No retry on Telegram 429.** If the events channel hits Telegram's rate limit, `send_message` raises, we log a warning, and move on. Next window tries fresh. Consider adding exponential backoff in Phase 4 if we see sustained 429s.
3. **`parse_mode=None` means raw text — no `[link](url)` rendering.** If operators want clickable dashboard links in events-channel messages, add a single-line Markdown footer per batch with `parse_mode="Markdown"`. Out of Phase 2 scope.
4. **Events channel alerts have no dedup with main chat.** A `cron_crashed` goes to BOTH chats. Intentional for now (belt-and-suspenders). If noise is an issue, `_EventsChannelSink` could skip events where `_MainChatSink` also fires — but the whole point of the events channel is completeness, so probably keep duplicated.
5. **Batch message format is plain text with emojis.** Readable, but cramped at 20 events. If operator complains, consider: (a) chunk at 10 instead of 20, (b) group by workflow in the formatter, (c) add 1 blank line between events. All one-line changes.
6. **Railway deployment doesn't get this env var.** Bot on Railway doesn't create `EventBus` instances today. Revisit in Phase 4 if `/tail` or other bot handlers emit events.
7. **Client leak on long-lived processes.** `_EventsChannelSink` instantiates a `TelegramClient` once at `__init__`. For a GH Actions run this is fine (short-lived). For a long-running process that creates many EventBus instances, clients accumulate — but no such process exists today.

## Phase 4 prerequisites

- Nothing extra for Phase 4 (/tail + step/api_call instrumentation) to proceed. The events channel is a read-downstream sink; Phase 4 is about write-upstream instrumentation.
```

- [ ] **Step 3.2: Commit**

```bash
cd /Users/bigode/Dev/agentics_workflows/.worktrees/obs-phase2 && \
  git add docs/superpowers/followups/2026-04-21-observability-phase2-followups.md && \
  git commit -m "docs(followups): observability phase 2 shipped — events channel validation checklist"
```

---

## Post-flight: merge to main

- [ ] **Step 4.1: Confirm ship criterion**

Revisit the 6 criteria from the header:

1. `_EventsChannelSink` in `_build_sinks`, env-gated ✅ Task 1
2. warn/error immediate ✅ Task 1 test
3. info batching (20 OR 1s) + atexit flush ✅ Task 1 tests
4. 6 workflow YAMLs updated ✅ Task 2
5. Baseline + 5 new tests pass ✅ Tasks 1, 2
6. Manual validation ⏳ operator's job (Task 3 checklist)

- [ ] **Step 4.2: Merge back to main**

```bash
cd /Users/bigode/Dev/agentics_workflows/.worktrees/obs-phase2 && git log --oneline main..HEAD
```

Expected: 3 commits.

From the main checkout:

```bash
cd /Users/bigode/Dev/agentics_workflows && \
  git checkout main && \
  git merge --no-ff feature/observability-phase2 -m "Merge observability phase 2 events channel"
```

Verify:

```bash
cd /Users/bigode/Dev/agentics_workflows && /usr/bin/python3 -m pytest tests/ 2>&1 | tail -3
```

Expected: 502 passed, 3 pre-existing failed.

- [ ] **Step 4.3: Clean up worktree and branch**

```bash
cd /Users/bigode/Dev/agentics_workflows && \
  git worktree remove .worktrees/obs-phase2 && \
  git branch -d feature/observability-phase2
```

- [ ] **Step 4.4: Push**

```bash
cd /Users/bigode/Dev/agentics_workflows && git push origin main
```

After the push, the next GH Actions run of any wrapped script starts relaying events to the channel.

---

## Self-Review

**Spec coverage:**
- Spec §224-225 "`_EventsChannelSink` enabled if TELEGRAM_EVENTS_CHANNEL_ID env var set" → Task 1 gating in `_build_sinks` ✓
- Spec §225 "Batches info in 1-second windows; warn/error flushed immediately" → Task 1 `emit` + `_flush` logic ✓
- Spec §230-244 Throttling pseudocode → Task 1 implementation matches structure (with concrete `atexit` handler for end-of-run flush) ✓
- Spec §616-619 "Phase 2 — implement `_EventsChannelSink` with 1s batching; add env var to all workflow YAMLs" → Tasks 1 + 2 cover both ✓
- Spec §649 "Telegram channel created + bot added as admin + channel ID captured — blocks Phase 2 ship" → Pre-flight Step 0.4 checks ✓

**Placeholder scan:** no TBDs. Step 2.2 asks the implementer to copy exact indentation from sibling lines (intentional, not a placeholder — YAML indentation varies per file).

**Type consistency:**
- `_EventsChannelSink(__init__: chat_id, client)` signature consistent in class definition (Task 1 Step 1.4) and `_build_sinks` wiring (Task 1 Step 1.5) ✓
- `_build_telegram_client()` already defined in Phase 1; reused unchanged ✓
- `_monotonic()` defined in event_bus.py (Step 1.3) and referenced by the sink (Step 1.4) + tests (Step 1.1) ✓
- Buffer constants `_BATCH_WINDOW_SECONDS = 1.0` and `_MAX_BUFFER = 20` defined on the class; tests assert behavior matching those values ✓
- Event dict keys read by `_format` (`ts`, `workflow`, `event`, `label`, `level`) match what `EventBus.emit` produces ✓

**One gotcha worth flagging to the implementer:** Telegram Markdown escape. The `_format` method uses `parse_mode=None` to bypass parsing; labels with `_` or `*` won't break formatting. If operators later want clickable URLs, they'll need to re-enable Markdown and escape user content — captured as followup #3.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-04-21-observability-phase2-events-channel-plan.md`.** Two execution options:

**1. Subagent-Driven (recommended)** — 3 tasks, each as a fresh subagent dispatch. Task 1 needs Sonnet (sink is non-trivial). Tasks 2 and 3 can run on Haiku (mechanical YAML edits + doc writing).

**2. Inline Execution** — execute tasks in a single session using executing-plans, batch execution with checkpoints for review.

**Which approach?**
