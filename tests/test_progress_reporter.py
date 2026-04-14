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
