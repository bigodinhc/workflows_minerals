"""daily_report delivery gate: telegram → channel publish, uazapi → legacy."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

import pytest


@pytest.fixture
def mocks():
    return MagicMock(), MagicMock(), MagicMock()  # progress, bus, logger


def test_telegram_mode_publishes_to_channel(monkeypatch, mocks):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "telegram")
    progress, bus, log = mocks
    import execution.scripts.send_daily_report as sdr
    publish = MagicMock(return_value={"ok": True, "message_id": 1, "error": None})
    with patch("execution.integrations.channel_publisher.publish_to_channel", publish), \
         patch.object(sdr, "ContactsRepo") as repo:
        sdr.deliver_message("relatório", False, progress, bus, log)
    publish.assert_called_once()
    assert publish.call_args.args[0] == "daily_report"
    assert publish.call_args.args[1] == "relatório"
    repo.assert_not_called()  # legacy path untouched
    progress.finish_empty.assert_called_once()


def test_telegram_mode_failure_raises(monkeypatch, mocks):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "telegram")
    progress, bus, log = mocks
    import execution.scripts.send_daily_report as sdr
    publish = MagicMock(return_value={"ok": False, "message_id": None, "error": "boom"})
    with patch("execution.integrations.channel_publisher.publish_to_channel", publish):
        with pytest.raises(RuntimeError, match="boom"):
            sdr.deliver_message("relatório", False, progress, bus, log)


def test_telegram_mode_dry_run_skips_publish(monkeypatch, mocks):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "telegram")
    progress, bus, log = mocks
    import execution.scripts.send_daily_report as sdr
    publish = MagicMock()
    with patch("execution.integrations.channel_publisher.publish_to_channel", publish):
        sdr.deliver_message("relatório", True, progress, bus, log)
    publish.assert_not_called()
    progress.finish_empty.assert_called_once_with("dry-run")


def test_published_workflow_type_is_client_routed(monkeypatch, mocks):
    """Guard: the workflow_type we publish must route to the channel."""
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "telegram")
    progress, bus, log = mocks
    import execution.scripts.send_daily_report as sdr
    publish = MagicMock(return_value={"ok": True, "message_id": 1, "error": None})
    with patch("execution.integrations.channel_publisher.publish_to_channel", publish):
        sdr.deliver_message("m", False, progress, bus, log)
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent / "webhook"))
    from bot.routing import CLIENT_WORKFLOWS
    assert publish.call_args.args[0] in CLIENT_WORKFLOWS


def test_uazapi_mode_uses_legacy_path(monkeypatch, mocks):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "uazapi")
    progress, bus, log = mocks
    import execution.scripts.send_daily_report as sdr
    publish = MagicMock()
    repo = MagicMock()
    repo.return_value.list_by_list_code.return_value = []  # no contacts → early return
    with patch("execution.integrations.channel_publisher.publish_to_channel", publish), \
         patch.object(sdr, "ContactsRepo", repo):
        sdr.deliver_message("relatório", False, progress, bus, log)
    publish.assert_not_called()
    repo.return_value.list_by_list_code.assert_called_once_with("minerals_report")
    progress.finish_empty.assert_called_once_with("nenhum contato ativo")
