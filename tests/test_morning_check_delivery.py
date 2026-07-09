"""morning_check delivery gate: telegram → channel publish, uazapi → legacy."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

import pytest


@pytest.fixture
def mocks():
    return MagicMock(), MagicMock(), MagicMock()  # progress, bus, logger


def test_telegram_mode_publishes_and_returns_true(monkeypatch, mocks):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "telegram")
    progress, bus, log = mocks
    import execution.scripts.morning_check as mc
    publish = MagicMock(return_value={"ok": True, "message_id": 2, "error": None})
    with patch("execution.integrations.channel_publisher.publish_to_channel", publish), \
         patch.object(mc, "ContactsRepo") as repo:
        sent = mc.deliver_message("preços platts", False, progress, bus, log)
    assert sent is True
    assert publish.call_args.args[0] == "morning_check"
    repo.assert_not_called()


def test_telegram_mode_failure_raises(monkeypatch, mocks):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "telegram")
    progress, bus, log = mocks
    import execution.scripts.morning_check as mc
    publish = MagicMock(return_value={"ok": False, "message_id": None, "error": "sem canal"})
    with patch("execution.integrations.channel_publisher.publish_to_channel", publish):
        with pytest.raises(RuntimeError, match="sem canal"):
            mc.deliver_message("m", False, progress, bus, log)


def test_telegram_mode_dry_run_returns_false(monkeypatch, mocks):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "telegram")
    progress, bus, log = mocks
    import execution.scripts.morning_check as mc
    publish = MagicMock()
    with patch("execution.integrations.channel_publisher.publish_to_channel", publish):
        sent = mc.deliver_message("m", True, progress, bus, log)
    assert sent is False
    publish.assert_not_called()


def test_published_workflow_type_is_client_routed(monkeypatch, mocks):
    """Guard: the workflow_type we publish must route to the channel."""
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "telegram")
    progress, bus, log = mocks
    import execution.scripts.morning_check as mc
    publish = MagicMock(return_value={"ok": True, "message_id": 1, "error": None})
    with patch("execution.integrations.channel_publisher.publish_to_channel", publish):
        mc.deliver_message("m", False, progress, bus, log)
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent / "webhook"))
    from bot.routing import CLIENT_WORKFLOWS
    assert publish.call_args.args[0] in CLIENT_WORKFLOWS


def test_uazapi_mode_no_contacts_returns_false(monkeypatch, mocks):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "uazapi")
    progress, bus, log = mocks
    import execution.scripts.morning_check as mc
    publish = MagicMock()
    repo = MagicMock()
    repo.return_value.list_by_list_code.return_value = []
    with patch("execution.integrations.channel_publisher.publish_to_channel", publish), \
         patch.object(mc, "ContactsRepo", repo):
        sent = mc.deliver_message("m", False, progress, bus, log)
    assert sent is False
    publish.assert_not_called()
    repo.return_value.list_by_list_code.assert_called_once_with("minerals_report")
