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


def test_finish_with_message_preview_includes_message():
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
    reporter.finish(report, message="Iron ore price: $100.50")

    kwargs = fake_client.edit_message_text.call_args.kwargs
    assert "Iron ore price: $100.50" in kwargs["new_text"]
    assert "Mensagem enviada" in kwargs["new_text"]


def test_finish_without_message_has_no_preview_section():
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
    reporter.finish(report)  # no message passed

    kwargs = fake_client.edit_message_text.call_args.kwargs
    assert "Mensagem enviada" not in kwargs["new_text"]


def test_finish_truncates_long_message_to_fit_telegram_limit():
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
    long_message = "X" * 10000  # well above Telegram's 4096 limit

    reporter.finish(report, message=long_message)

    kwargs = fake_client.edit_message_text.call_args.kwargs
    # Must stay under Telegram limit
    assert len(kwargs["new_text"]) <= 4096
    # Must contain truncation marker
    assert "truncada" in kwargs["new_text"]
