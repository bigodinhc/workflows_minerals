# Workflow Progress Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a `ProgressReporter` that sends one Telegram message at workflow start and edits it throughout the run (preparing → sending X/N → final summary), replacing the current end-only notification for all 5 scheduled workflows.

**Architecture:** A new `ProgressReporter` class in `execution/core/progress_reporter.py` wraps the existing `TelegramClient`, keeps the `message_id` in memory for the single Python process that is each workflow, and hooks into `DeliveryReporter` via its existing `on_progress` callback. The `DeliveryReporter` module is **not modified** — scripts pass `notify_telegram=False` and `on_progress=progress.on_dispatch_tick`. Cadence for in-dispatch edits: every 10% progressed OR every 5s OR on the final tick, whichever fires first.

**Tech Stack:** Python 3.9, pytest, `requests` (already used by TelegramClient), `unittest.mock` for tests. No new dependencies.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `execution/core/progress_reporter.py` | `ProgressReporter` class — single-message Telegram lifecycle | Create |
| `tests/test_progress_reporter.py` | Unit + integration tests for `ProgressReporter` | Create |
| `execution/scripts/morning_check.py` | Wire `ProgressReporter` around existing flow | Modify |
| `execution/scripts/send_daily_report.py` | Wire `ProgressReporter` around existing flow | Modify |
| `execution/scripts/baltic_ingestion.py` | Wire `ProgressReporter` around existing flow | Modify |
| `execution/scripts/send_news.py` | Wire `ProgressReporter` around existing flow (covers market_news and rationale_news) | Modify |

`execution/core/delivery_reporter.py` is **not modified**. `execution/integrations/telegram_client.py` is **not modified** — `send_message` and `edit_message_text` already exist.

---

### Task 1: Create `ProgressReporter` skeleton — start() and disabled state

**Files:**
- Create: `execution/core/progress_reporter.py`
- Test: `tests/test_progress_reporter.py`

- [ ] **Step 1: Write the failing tests for start() and disabled state**

Create `tests/test_progress_reporter.py`:

```python
"""Tests for execution.core.progress_reporter module."""
from unittest.mock import MagicMock
from execution.core.progress_reporter import ProgressReporter


def test_start_stores_message_id_from_telegram():
    fake_client = MagicMock()
    fake_client.send_message.return_value = 42
    reporter = ProgressReporter(
        workflow="morning_check",
        chat_id="chat-1",
        telegram_client=fake_client,
    )
    reporter.start("Preparando dados...")
    assert reporter._message_id == 42
    assert reporter._disabled is False
    fake_client.send_message.assert_called_once()
    call_kwargs = fake_client.send_message.call_args.kwargs
    assert call_kwargs["chat_id"] == "chat-1"
    assert "MORNING CHECK" in call_kwargs["text"]
    assert "Preparando dados..." in call_kwargs["text"]
    assert "⏳" in call_kwargs["text"]


def test_start_marks_disabled_when_send_returns_none():
    fake_client = MagicMock()
    fake_client.send_message.return_value = None
    reporter = ProgressReporter(
        workflow="test",
        chat_id="chat-1",
        telegram_client=fake_client,
    )
    reporter.start()
    assert reporter._message_id is None
    assert reporter._disabled is True


def test_start_marks_disabled_when_send_raises():
    fake_client = MagicMock()
    fake_client.send_message.side_effect = RuntimeError("telegram down")
    reporter = ProgressReporter(
        workflow="test",
        chat_id="chat-1",
        telegram_client=fake_client,
    )
    reporter.start()
    assert reporter._message_id is None
    assert reporter._disabled is True


def test_start_uses_default_phase_text():
    fake_client = MagicMock()
    fake_client.send_message.return_value = 1
    reporter = ProgressReporter(
        workflow="test",
        chat_id="chat-1",
        telegram_client=fake_client,
    )
    reporter.start()
    text = fake_client.send_message.call_args.kwargs["text"]
    assert "Preparando dados..." in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_progress_reporter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'execution.core.progress_reporter'`

- [ ] **Step 3: Create the module with start() only**

Create `execution/core/progress_reporter.py`:

```python
"""
Progress reporter: sends one Telegram message at workflow start and edits it
throughout the run. Designed to be used alongside DeliveryReporter (pass
on_progress=progress.on_dispatch_tick and notify_telegram=False).

All methods are non-raising. Telegram failures degrade to log warnings so
the workflow is never broken by a notification failure.
"""
from datetime import datetime
from typing import Optional


class ProgressReporter:
    def __init__(
        self,
        workflow: str,
        chat_id: Optional[str] = None,
        dashboard_base_url: str = "https://workflows-minerals.vercel.app",
        gh_run_id: Optional[str] = None,
        telegram_client=None,
    ):
        self.workflow = workflow
        self.chat_id = chat_id
        self.dashboard_base_url = dashboard_base_url
        self.gh_run_id = gh_run_id
        self._telegram_client = telegram_client
        self._message_id: Optional[int] = None
        self._disabled: bool = False
        self._last_edit_at: float = 0.0
        self._last_edit_pct: int = 0
        self._started_at: Optional[datetime] = None

    def _get_client(self):
        if self._telegram_client is not None:
            return self._telegram_client
        from execution.integrations.telegram_client import TelegramClient
        self._telegram_client = TelegramClient()
        return self._telegram_client

    def _header(self, emoji: str, body: str) -> str:
        title = self.workflow.upper().replace("_", " ")
        started = self._started_at or datetime.now().astimezone()
        when = started.strftime("%d/%m/%Y %H:%M")
        return f"{emoji} {title}\n{when}\n{body}"

    def start(self, phase_text: str = "Preparando dados...") -> None:
        """Send initial message and store message_id. Never raises."""
        self._started_at = datetime.now().astimezone()
        text = self._header("⏳", phase_text)
        try:
            client = self._get_client()
            message_id = client.send_message(text=text, chat_id=self.chat_id)
        except Exception as exc:
            print(f"[WARN] ProgressReporter.start failed: {exc}")
            self._disabled = True
            return
        if message_id is None:
            self._disabled = True
            return
        self._message_id = message_id
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_progress_reporter.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/core/progress_reporter.py tests/test_progress_reporter.py && git commit -m "feat: add ProgressReporter skeleton with start() and disabled state"
```

---

### Task 2: Add `update()` method

**Files:**
- Modify: `execution/core/progress_reporter.py`
- Test: `tests/test_progress_reporter.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_progress_reporter.py`:

```python
def test_update_edits_message_when_enabled():
    fake_client = MagicMock()
    fake_client.send_message.return_value = 42
    fake_client.edit_message_text.return_value = True
    reporter = ProgressReporter(
        workflow="test",
        chat_id="chat-1",
        telegram_client=fake_client,
    )
    reporter.start("Preparando dados...")
    fake_client.reset_mock()

    reporter.update("Processing step 2...")

    fake_client.edit_message_text.assert_called_once()
    kwargs = fake_client.edit_message_text.call_args.kwargs
    assert kwargs["chat_id"] == "chat-1"
    assert kwargs["message_id"] == 42
    assert "Processing step 2..." in kwargs["new_text"]
    assert "TEST" in kwargs["new_text"]


def test_update_noop_when_disabled():
    fake_client = MagicMock()
    fake_client.send_message.return_value = None  # start fails → disabled
    reporter = ProgressReporter(
        workflow="test",
        chat_id="chat-1",
        telegram_client=fake_client,
    )
    reporter.start()
    fake_client.reset_mock()

    reporter.update("anything")

    fake_client.edit_message_text.assert_not_called()


def test_update_swallows_exceptions():
    fake_client = MagicMock()
    fake_client.send_message.return_value = 42
    fake_client.edit_message_text.side_effect = RuntimeError("telegram down")
    reporter = ProgressReporter(
        workflow="test",
        chat_id="chat-1",
        telegram_client=fake_client,
    )
    reporter.start()
    # Must not raise
    reporter.update("anything")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_progress_reporter.py -v`
Expected: FAIL on 3 new tests — `AttributeError: 'ProgressReporter' object has no attribute 'update'`

- [ ] **Step 3: Add update() method**

Append to `execution/core/progress_reporter.py` inside the class:

```python
    def update(self, text: str) -> None:
        """Edit the current message with new body text. Never raises."""
        if self._disabled or self._message_id is None:
            return
        full = self._header("⏳", text)
        try:
            client = self._get_client()
            client.edit_message_text(
                chat_id=self.chat_id,
                message_id=self._message_id,
                new_text=full,
            )
        except Exception as exc:
            print(f"[WARN] ProgressReporter.update failed: {exc}")
        import time as _time
        self._last_edit_at = _time.monotonic()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_progress_reporter.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/core/progress_reporter.py tests/test_progress_reporter.py && git commit -m "feat: add ProgressReporter.update() with error swallowing"
```

---

### Task 3: Add `on_dispatch_tick()` with cadence throttle

**Files:**
- Modify: `execution/core/progress_reporter.py`
- Test: `tests/test_progress_reporter.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_progress_reporter.py`:

```python
from execution.core.delivery_reporter import Contact, DeliveryResult


def _dummy_result():
    return DeliveryResult(
        contact=Contact(name="x", phone="1"),
        success=True,
        error=None,
        duration_ms=0,
    )


def test_on_dispatch_tick_no_edit_before_10_percent(monkeypatch):
    fake_client = MagicMock()
    fake_client.send_message.return_value = 42
    fake_client.edit_message_text.return_value = True

    fake_time = [100.0]
    monkeypatch.setattr("time.monotonic", lambda: fake_time[0])

    reporter = ProgressReporter(
        workflow="test",
        chat_id="chat-1",
        telegram_client=fake_client,
    )
    reporter.start()
    fake_client.reset_mock()

    # 5/100 = 5%, below 10% threshold
    reporter.on_dispatch_tick(5, 100, _dummy_result())
    fake_client.edit_message_text.assert_not_called()


def test_on_dispatch_tick_edits_at_10_percent(monkeypatch):
    fake_client = MagicMock()
    fake_client.send_message.return_value = 42
    fake_client.edit_message_text.return_value = True

    fake_time = [100.0]
    monkeypatch.setattr("time.monotonic", lambda: fake_time[0])

    reporter = ProgressReporter(
        workflow="test",
        chat_id="chat-1",
        telegram_client=fake_client,
    )
    reporter.start()
    fake_client.reset_mock()

    reporter.on_dispatch_tick(10, 100, _dummy_result())
    fake_client.edit_message_text.assert_called_once()
    kwargs = fake_client.edit_message_text.call_args.kwargs
    assert "(10/100)" in kwargs["new_text"]
    assert "📤" in kwargs["new_text"]


def test_on_dispatch_tick_edits_after_5_seconds(monkeypatch):
    fake_client = MagicMock()
    fake_client.send_message.return_value = 42
    fake_client.edit_message_text.return_value = True

    fake_time = [100.0]
    monkeypatch.setattr("time.monotonic", lambda: fake_time[0])

    reporter = ProgressReporter(
        workflow="test",
        chat_id="chat-1",
        telegram_client=fake_client,
    )
    reporter.start()
    fake_client.reset_mock()

    # Below 10% threshold
    reporter.on_dispatch_tick(3, 100, _dummy_result())
    fake_client.edit_message_text.assert_not_called()

    # Advance time past 5s, still below 10%
    fake_time[0] = 106.0
    reporter.on_dispatch_tick(4, 100, _dummy_result())
    fake_client.edit_message_text.assert_called_once()


def test_on_dispatch_tick_always_edits_on_final(monkeypatch):
    fake_client = MagicMock()
    fake_client.send_message.return_value = 42
    fake_client.edit_message_text.return_value = True

    fake_time = [100.0]
    monkeypatch.setattr("time.monotonic", lambda: fake_time[0])

    reporter = ProgressReporter(
        workflow="test",
        chat_id="chat-1",
        telegram_client=fake_client,
    )
    reporter.start()
    fake_client.reset_mock()

    # On very small lists, the only tick is processed == total
    reporter.on_dispatch_tick(3, 3, _dummy_result())
    fake_client.edit_message_text.assert_called_once()
    kwargs = fake_client.edit_message_text.call_args.kwargs
    assert "(3/3)" in kwargs["new_text"]


def test_on_dispatch_tick_noop_when_disabled():
    fake_client = MagicMock()
    fake_client.send_message.return_value = None  # disabled
    reporter = ProgressReporter(
        workflow="test",
        chat_id="chat-1",
        telegram_client=fake_client,
    )
    reporter.start()
    fake_client.reset_mock()

    reporter.on_dispatch_tick(50, 100, _dummy_result())
    fake_client.edit_message_text.assert_not_called()


def test_on_dispatch_tick_throttle_count_for_100_contacts(monkeypatch):
    """For a 100-contact dispatch with near-zero time between ticks, we expect
    ~10 edits (one per 10% step), never more than 12 (including start)."""
    fake_client = MagicMock()
    fake_client.send_message.return_value = 42
    fake_client.edit_message_text.return_value = True

    fake_time = [100.0]
    monkeypatch.setattr("time.monotonic", lambda: fake_time[0])

    reporter = ProgressReporter(
        workflow="test",
        chat_id="chat-1",
        telegram_client=fake_client,
    )
    reporter.start()
    fake_client.reset_mock()

    for i in range(1, 101):
        reporter.on_dispatch_tick(i, 100, _dummy_result())

    edit_count = fake_client.edit_message_text.call_count
    assert 9 <= edit_count <= 12, f"Expected 9-12 edits, got {edit_count}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_progress_reporter.py -v`
Expected: FAIL with `AttributeError: 'ProgressReporter' object has no attribute 'on_dispatch_tick'`

- [ ] **Step 3: Add on_dispatch_tick()**

Append to `execution/core/progress_reporter.py` inside the class:

```python
    def on_dispatch_tick(self, processed: int, total: int, result) -> None:
        """Called once per DeliveryReporter progress event. Throttles edits.
        Edits when any of: (pct delta >= 10) OR (>=5s since last edit) OR
        (processed == total, force final).
        """
        if self._disabled or self._message_id is None or total <= 0:
            return
        import time as _time
        now = _time.monotonic()
        pct = int(processed * 100 / total)
        pct_delta = pct - self._last_edit_pct
        time_delta = now - self._last_edit_at
        is_final = processed == total

        should_edit = (pct_delta >= 10) or (time_delta >= 5.0) or is_final
        if not should_edit:
            return

        body = f"📤 Enviando pra {total} contatos... ({processed}/{total})"
        full = self._header("⏳", body)
        try:
            client = self._get_client()
            client.edit_message_text(
                chat_id=self.chat_id,
                message_id=self._message_id,
                new_text=full,
            )
        except Exception as exc:
            print(f"[WARN] ProgressReporter.on_dispatch_tick edit failed: {exc}")

        self._last_edit_at = now
        self._last_edit_pct = pct
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_progress_reporter.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/core/progress_reporter.py tests/test_progress_reporter.py && git commit -m "feat: add ProgressReporter.on_dispatch_tick with 10%/5s throttle"
```

---

### Task 4: Add `finish()` and `finish_empty()`

**Files:**
- Modify: `execution/core/progress_reporter.py`
- Test: `tests/test_progress_reporter.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_progress_reporter.py`:

```python
from datetime import datetime
from execution.core.delivery_reporter import DeliveryReport


def _make_report(workflow, results):
    now = datetime.now().astimezone()
    return DeliveryReport(
        workflow=workflow,
        started_at=now,
        finished_at=now,
        results=results,
    )


def test_finish_edits_with_success_emoji_for_all_success():
    fake_client = MagicMock()
    fake_client.send_message.return_value = 42
    fake_client.edit_message_text.return_value = True
    reporter = ProgressReporter(
        workflow="test",
        chat_id="chat-1",
        telegram_client=fake_client,
    )
    reporter.start()
    fake_client.reset_mock()

    results = [
        DeliveryResult(contact=Contact(name="A", phone="1"), success=True, error=None, duration_ms=0)
    ]
    report = _make_report("test", results)
    reporter.finish(report)

    fake_client.edit_message_text.assert_called_once()
    kwargs = fake_client.edit_message_text.call_args.kwargs
    assert kwargs["chat_id"] == "chat-1"
    assert kwargs["message_id"] == 42
    assert "✅" in kwargs["new_text"]
    assert "Total: 1" in kwargs["new_text"]


def test_finish_edits_with_total_failure_emoji():
    fake_client = MagicMock()
    fake_client.send_message.return_value = 42
    fake_client.edit_message_text.return_value = True
    reporter = ProgressReporter(
        workflow="test",
        chat_id="chat-1",
        telegram_client=fake_client,
    )
    reporter.start()
    fake_client.reset_mock()

    results = [
        DeliveryResult(contact=Contact(name=f"U{i}", phone=str(i)), success=False, error="boom", duration_ms=0)
        for i in range(10)
    ]
    report = _make_report("test", results)
    reporter.finish(report)

    kwargs = fake_client.edit_message_text.call_args.kwargs
    assert "🚨" in kwargs["new_text"]
    assert "FALHA TOTAL" in kwargs["new_text"]


def test_finish_swallows_exceptions():
    fake_client = MagicMock()
    fake_client.send_message.return_value = 42
    fake_client.edit_message_text.side_effect = RuntimeError("telegram down")
    reporter = ProgressReporter(
        workflow="test",
        chat_id="chat-1",
        telegram_client=fake_client,
    )
    reporter.start()

    results = [
        DeliveryResult(contact=Contact(name="A", phone="1"), success=True, error=None, duration_ms=0)
    ]
    report = _make_report("test", results)
    # Must not raise
    reporter.finish(report)


def test_finish_noop_when_disabled():
    fake_client = MagicMock()
    fake_client.send_message.return_value = None  # disabled
    reporter = ProgressReporter(
        workflow="test",
        chat_id="chat-1",
        telegram_client=fake_client,
    )
    reporter.start()
    fake_client.reset_mock()

    results = [
        DeliveryResult(contact=Contact(name="A", phone="1"), success=True, error=None, duration_ms=0)
    ]
    report = _make_report("test", results)
    reporter.finish(report)

    fake_client.edit_message_text.assert_not_called()


def test_finish_empty_edits_with_info_emoji_and_reason():
    fake_client = MagicMock()
    fake_client.send_message.return_value = 42
    fake_client.edit_message_text.return_value = True
    reporter = ProgressReporter(
        workflow="market_news",
        chat_id="chat-1",
        telegram_client=fake_client,
    )
    reporter.start()
    fake_client.reset_mock()

    reporter.finish_empty("sem items novos")

    fake_client.edit_message_text.assert_called_once()
    kwargs = fake_client.edit_message_text.call_args.kwargs
    assert "ℹ️" in kwargs["new_text"]
    assert "sem items novos" in kwargs["new_text"]
    assert "MARKET NEWS" in kwargs["new_text"]


def test_finish_empty_noop_when_disabled():
    fake_client = MagicMock()
    fake_client.send_message.return_value = None  # disabled
    reporter = ProgressReporter(
        workflow="test",
        chat_id="chat-1",
        telegram_client=fake_client,
    )
    reporter.start()
    fake_client.reset_mock()

    reporter.finish_empty("nothing")
    fake_client.edit_message_text.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_progress_reporter.py -v`
Expected: FAIL with `AttributeError: 'ProgressReporter' object has no attribute 'finish'`

- [ ] **Step 3: Add finish() and finish_empty()**

Append to `execution/core/progress_reporter.py` inside the class:

```python
    def finish(self, report) -> None:
        """Edit message with final summary. Reuses _format_telegram_message
        from delivery_reporter for format parity with today's notification.
        Never raises."""
        if self._disabled or self._message_id is None:
            return
        from execution.core.delivery_reporter import _format_telegram_message
        try:
            text = _format_telegram_message(
                report,
                dashboard_base_url=self.dashboard_base_url,
                gh_run_id=self.gh_run_id,
            )
        except Exception as exc:
            print(f"[WARN] ProgressReporter.finish format failed: {exc}")
            return
        try:
            client = self._get_client()
            client.edit_message_text(
                chat_id=self.chat_id,
                message_id=self._message_id,
                new_text=text,
            )
        except Exception as exc:
            print(f"[WARN] ProgressReporter.finish edit failed: {exc}")

    def finish_empty(self, reason: str) -> None:
        """Edit message to signal a no-op finish (e.g., no new articles)."""
        if self._disabled or self._message_id is None:
            return
        text = self._header("ℹ️", reason)
        try:
            client = self._get_client()
            client.edit_message_text(
                chat_id=self.chat_id,
                message_id=self._message_id,
                new_text=text,
            )
        except Exception as exc:
            print(f"[WARN] ProgressReporter.finish_empty edit failed: {exc}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_progress_reporter.py -v`
Expected: 19 passed

- [ ] **Step 5: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/core/progress_reporter.py tests/test_progress_reporter.py && git commit -m "feat: add ProgressReporter.finish() and finish_empty()"
```

---

### Task 5: Full-lifecycle integration test

**Files:**
- Test: `tests/test_progress_reporter.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_progress_reporter.py`:

```python
def test_full_lifecycle_start_dispatch_finish(monkeypatch):
    """Integration: start → 100 ticks → finish. Verify call sequence and
    that finish text matches what _format_telegram_message would produce."""
    fake_client = MagicMock()
    fake_client.send_message.return_value = 999
    fake_client.edit_message_text.return_value = True

    fake_time = [200.0]
    monkeypatch.setattr("time.monotonic", lambda: fake_time[0])

    reporter = ProgressReporter(
        workflow="morning_check",
        chat_id="chat-x",
        gh_run_id="RUN123",
        telegram_client=fake_client,
    )
    reporter.start("Buscando dados...")

    # Simulate 100-contact dispatch
    for i in range(1, 101):
        reporter.on_dispatch_tick(i, 100, _dummy_result())

    results = [
        DeliveryResult(contact=Contact(name=f"U{i}", phone=str(i)), success=True, error=None, duration_ms=0)
        for i in range(100)
    ]
    report = _make_report("morning_check", results)
    reporter.finish(report)

    # 1 sendMessage (start) + at least 10 edits (10% steps + final) + 1 finish edit
    assert fake_client.send_message.call_count == 1
    assert fake_client.edit_message_text.call_count >= 10

    # Final edit should contain the summary
    final_call = fake_client.edit_message_text.call_args_list[-1]
    assert "✅" in final_call.kwargs["new_text"]
    assert "Total: 100" in final_call.kwargs["new_text"]
    assert "RUN123" in final_call.kwargs["new_text"]  # link includes run_id


def test_update_called_before_start_is_noop():
    """Calling update() before start() must be a no-op, not a crash."""
    fake_client = MagicMock()
    reporter = ProgressReporter(
        workflow="test",
        chat_id="chat-1",
        telegram_client=fake_client,
    )
    # Do not call start()
    reporter.update("anything")
    fake_client.edit_message_text.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_progress_reporter.py -v`
Expected: 21 passed (these tests exercise code already written; any failure here indicates a bug in prior tasks)

- [ ] **Step 3: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add tests/test_progress_reporter.py && git commit -m "test: add ProgressReporter full-lifecycle integration test"
```

---

### Task 6: Wire `ProgressReporter` into `morning_check.py`

**Files:**
- Modify: `execution/scripts/morning_check.py`

- [ ] **Step 1: Update imports**

In `execution/scripts/morning_check.py`, change line 17:

```python
from execution.core.delivery_reporter import DeliveryReporter, Contact, build_contact_from_row
```

to:

```python
from execution.core.delivery_reporter import DeliveryReporter, Contact, build_contact_from_row
from execution.core.progress_reporter import ProgressReporter
```

- [ ] **Step 2: Wrap main body with ProgressReporter**

Locate line 196 (`logger.info(f"Starting Morning Check for {date_str}")`) and insert immediately after it:

```python
    progress = ProgressReporter(
        workflow="morning_check",
        chat_id=os.getenv("TELEGRAM_CHAT_ID"),
        gh_run_id=os.getenv("GITHUB_RUN_ID"),
    )
    progress.start("Preparando dados...")
```

- [ ] **Step 3: Update the DeliveryReporter block and add finish()**

Replace the block at lines 257–266 (the existing `reporter = DeliveryReporter(...)` through `logger.info(f"Broadcast complete..."`) with:

```python
    progress.update(f"Enviando pra {len(delivery_contacts)} contatos... (0/{len(delivery_contacts)})")

    reporter = DeliveryReporter(
        workflow="morning_check",
        send_fn=uazapi.send_message,
        notify_telegram=False,
        gh_run_id=os.getenv("GITHUB_RUN_ID"),
    )
    report = reporter.dispatch(
        delivery_contacts,
        message,
        on_progress=progress.on_dispatch_tick,
    )

    progress.finish(report)

    logger.info(
        f"Broadcast complete. Sent: {report.success_count}, Failed: {report.failure_count}"
    )
```

- [ ] **Step 4: Handle the early-exit paths**

Three places in `morning_check.py` exit before dispatch:

1. Line 213 (`No data available yet from Platts. Will retry later.`) — before `sys.exit(0)`, add:
   ```python
       progress.finish_empty("sem dados do Platts ainda")
   ```

2. Line 224 (`Threshold is {MIN_ITEMS_EXPECTED}...`) — before `sys.exit(0)`, add:
   ```python
       progress.finish_empty(f"dados incompletos ({len(report_items)}/{TOTAL_SYMBOLS})")
   ```

3. Line 247 (`logger.warning("No contacts found.")`) — before `return`, add:
   ```python
       progress.finish_empty("nenhum contato ativo")
   ```

Also add `progress.finish_empty("report ja enviado hoje")` before `return` at line 204 (inside the `if sheets.check_daily_status(...)` block).

- [ ] **Step 5: Verify the script still imports**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -c "import execution.scripts.morning_check"`
Expected: no output (no ImportError)

- [ ] **Step 6: Run the full test suite**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest -v`
Expected: all tests pass (ProgressReporter + pre-existing DeliveryReporter + others)

- [ ] **Step 7: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/scripts/morning_check.py && git commit -m "feat: wire ProgressReporter into morning_check workflow"
```

---

### Task 7: Wire `ProgressReporter` into `send_daily_report.py`

**Files:**
- Modify: `execution/scripts/send_daily_report.py`

- [ ] **Step 1: Read the file and locate insertion points**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && grep -n "DeliveryReporter\|sys.exit\|return" execution/scripts/send_daily_report.py`

Identify:
- The top-of-file imports block
- The `DeliveryReporter(...)` call (around line 141)
- Any early-exit paths (no data, no contacts)

- [ ] **Step 2: Add imports**

Add alongside the existing `from execution.core.delivery_reporter import ...` line:

```python
from execution.core.progress_reporter import ProgressReporter
```

- [ ] **Step 3: Create ProgressReporter at start of main()**

After the logger is initialized in `main()`, insert:

```python
    progress = ProgressReporter(
        workflow="daily_report",
        chat_id=os.getenv("TELEGRAM_CHAT_ID"),
        gh_run_id=os.getenv("GITHUB_RUN_ID"),
    )
    progress.start("Preparando dados...")
```

- [ ] **Step 4: Replace the DeliveryReporter block**

Replace the existing `reporter = DeliveryReporter(...)` and `reporter.dispatch(...)` block (around lines 141–146) with:

```python
        progress.update(f"Enviando pra {len(delivery_contacts)} contatos... (0/{len(delivery_contacts)})")

        reporter = DeliveryReporter(
            workflow="daily_report",
            send_fn=uazapi.send_message,
            notify_telegram=False,
            gh_run_id=os.getenv("GITHUB_RUN_ID"),
        )
        report = reporter.dispatch(
            delivery_contacts,
            message,
            on_progress=progress.on_dispatch_tick,
        )
        progress.finish(report)
```

- [ ] **Step 5: Add finish_empty() to early-exit paths**

For every path in `main()` that exits before `reporter.dispatch()` runs (no data, no contacts, already-sent check), add a `progress.finish_empty("<reason>")` call immediately before `return` or `sys.exit(...)`. Use reasons analogous to the morning_check ones:
- No new data available: `progress.finish_empty("sem dados do Platts ainda")`
- Incomplete data: `progress.finish_empty("dados incompletos")`
- Already sent today: `progress.finish_empty("report ja enviado hoje")`
- No contacts: `progress.finish_empty("nenhum contato ativo")`

Read each early-exit site and apply the most appropriate reason string.

- [ ] **Step 6: Verify import and tests**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -c "import execution.scripts.send_daily_report" && python -m pytest -v`
Expected: no import error, all tests pass

- [ ] **Step 7: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/scripts/send_daily_report.py && git commit -m "feat: wire ProgressReporter into daily_report workflow"
```

---

### Task 8: Wire `ProgressReporter` into `baltic_ingestion.py`

**Files:**
- Modify: `execution/scripts/baltic_ingestion.py`

- [ ] **Step 1: Read the file to find the DeliveryReporter block and exits**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && grep -n "DeliveryReporter\|sys.exit\|^def \|return" execution/scripts/baltic_ingestion.py`

- [ ] **Step 2: Add import**

Add alongside the existing `from execution.core.delivery_reporter import ...` line:

```python
from execution.core.progress_reporter import ProgressReporter
```

- [ ] **Step 3: Initialize ProgressReporter**

Right after logger initialization in `main()`, add:

```python
    progress = ProgressReporter(
        workflow="baltic",
        chat_id=os.getenv("TELEGRAM_CHAT_ID_BALTIC") or os.getenv("TELEGRAM_CHAT_ID"),
        gh_run_id=os.getenv("GITHUB_RUN_ID"),
    )
    progress.start("Preparando dados Baltic...")
```

**Note:** Baltic workflow sends to a separate Telegram group in production. Use `TELEGRAM_CHAT_ID_BALTIC` if set, else fall back to `TELEGRAM_CHAT_ID`. If neither is set, the client falls back to its hardcoded default (same behavior as today). If the existing `baltic_ingestion.py` already uses a different env var name for its chat routing, use that name here instead.

- [ ] **Step 4: Replace the DeliveryReporter block**

Replace the block at lines ~276–281 with:

```python
    progress.update(f"Enviando pra {len(delivery_contacts)} contatos... (0/{len(delivery_contacts)})")

    reporter = DeliveryReporter(
        workflow="baltic",
        send_fn=uazapi.send_message,
        notify_telegram=False,
        gh_run_id=os.getenv("GITHUB_RUN_ID"),
    )
    report = reporter.dispatch(
        delivery_contacts,
        message,
        on_progress=progress.on_dispatch_tick,
    )
    progress.finish(report)
```

- [ ] **Step 5: Handle early-exit paths with finish_empty()**

For every `return` or `sys.exit(...)` in `main()` that occurs before `reporter.dispatch()` runs, add `progress.finish_empty("<reason>")` immediately before. Typical reasons for baltic:
- No Baltic data: `progress.finish_empty("sem dados Baltic disponiveis")`
- No contacts: `progress.finish_empty("nenhum contato ativo")`
- Already sent: `progress.finish_empty("report ja enviado hoje")`

- [ ] **Step 6: Verify import and tests**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -c "import execution.scripts.baltic_ingestion" && python -m pytest -v`
Expected: no import error, all tests pass

- [ ] **Step 7: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/scripts/baltic_ingestion.py && git commit -m "feat: wire ProgressReporter into baltic_ingestion workflow"
```

---

### Task 9: Wire `ProgressReporter` into `send_news.py` (market_news + rationale_news)

**Files:**
- Modify: `execution/scripts/send_news.py`

**Context:** `send_news.py` is invoked by both `market_news.yml` and `rationale_news.yml` (different profiles, same script). The `workflow` label for `ProgressReporter` must reflect which profile is running. Check how the script selects profile (CLI arg or env var) before implementing.

- [ ] **Step 1: Read the file**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && grep -n "DeliveryReporter\|sys.exit\|argparse\|workflow=\|return" execution/scripts/send_news.py`

Identify:
- How the profile/workflow name is determined (argparse flag? env var?)
- The DeliveryReporter block around line 58
- Early-exit paths (no new articles, no contacts, Claude failure)

- [ ] **Step 2: Add import**

Add alongside the existing `from execution.core.delivery_reporter import ...` line:

```python
from execution.core.progress_reporter import ProgressReporter
```

- [ ] **Step 3: Initialize ProgressReporter using the resolved workflow name**

After the profile/workflow label is determined (e.g., after argparse.parse_args() or after reading the profile env var), add:

```python
    progress = ProgressReporter(
        workflow=workflow_name,  # e.g. "market_news" or "rationale_news"
        chat_id=os.getenv("TELEGRAM_CHAT_ID"),
        gh_run_id=os.getenv("GITHUB_RUN_ID"),
    )
    progress.start("Preparando dados...")
```

Use whatever local variable holds the workflow label. If the current code passes the label as a hardcoded string literal `"manual_news"` at line 59, replace that literal with the same `workflow_name` variable so script label and progress label stay in sync.

- [ ] **Step 4: Replace the DeliveryReporter block**

Replace the existing block around lines 58–63 with:

```python
    progress.update(f"Enviando pra {len(delivery_contacts)} contatos... (0/{len(delivery_contacts)})")

    reporter = DeliveryReporter(
        workflow=workflow_name,
        send_fn=uazapi.send_message,
        notify_telegram=False,
        gh_run_id=os.getenv("GITHUB_RUN_ID"),
    )
    report = reporter.dispatch(
        delivery_contacts,
        msg,
        on_progress=progress.on_dispatch_tick,
    )
    progress.finish(report)
```

- [ ] **Step 5: Handle early-exit paths**

For every `return` or `sys.exit(...)` in `main()` before `reporter.dispatch()`, add:
- No new articles: `progress.finish_empty("sem noticias novas")`
- No contacts: `progress.finish_empty("nenhum contato ativo")`
- Claude/translation failure: `progress.finish_empty("falha ao processar noticias")`

- [ ] **Step 6: Verify import and tests**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -c "import execution.scripts.send_news" && python -m pytest -v`
Expected: no import error, all tests pass

- [ ] **Step 7: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/scripts/send_news.py && git commit -m "feat: wire ProgressReporter into send_news workflow"
```

---

### Task 10: Final verification

- [ ] **Step 1: Run the entire test suite**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest -v`
Expected: all tests pass. Count the ProgressReporter tests — should be 21.

- [ ] **Step 2: Smoke-import every modified script**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && python -c "
import execution.scripts.morning_check
import execution.scripts.send_daily_report
import execution.scripts.baltic_ingestion
import execution.scripts.send_news
print('all scripts import ok')
"
```
Expected: `all scripts import ok`

- [ ] **Step 3: Dry-run morning_check**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m execution.scripts.morning_check --dry-run`
Expected: script runs without error. Does not actually send WhatsApp. Does still call Telegram `start()` — note that locally you may not have `TELEGRAM_BOT_TOKEN` set, in which case `TelegramClient.__init__` raises `ValueError` and the `start()` call logs a warning and disables the reporter. That is acceptable — workflow continues.

If local env is missing `TELEGRAM_BOT_TOKEN`, skip this dry-run or export a dummy token for the test.

- [ ] **Step 4: Commit if any final tweaks were needed, otherwise proceed**

No commit needed if nothing changed. If any script needed a small fix during verification, commit it now:

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add -A && git commit -m "fix: minor adjustments from smoke-test"
```
