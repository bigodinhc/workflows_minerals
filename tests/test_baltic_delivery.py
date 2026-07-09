"""baltic_ingestion delivery gate: telegram → channel publish, uazapi → legacy."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

import pytest


@pytest.fixture
def mocks():
    reporter = MagicMock()
    reporter.step = AsyncMock()
    reporter.finish = AsyncMock()
    return reporter, MagicMock(), MagicMock()  # reporter, bus, logger


@pytest.mark.asyncio
async def test_telegram_mode_publishes_and_finishes(monkeypatch, mocks):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "telegram")
    reporter, bus, log = mocks
    import execution.scripts.baltic_ingestion as bi
    publish = MagicMock(return_value={"ok": True, "message_id": 3, "error": None})
    with patch("execution.integrations.channel_publisher.publish_to_channel", publish), \
         patch.object(bi, "ContactsRepo") as repo:
        sent = await bi.deliver_message("bdi msg", False, reporter, bus, log)
    assert sent is True
    assert publish.call_args.args[0] == bi.WORKFLOW_NAME
    repo.assert_not_called()
    reporter.finish.assert_awaited_once()


@pytest.mark.asyncio
async def test_telegram_mode_failure_raises(monkeypatch, mocks):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "telegram")
    reporter, bus, log = mocks
    import execution.scripts.baltic_ingestion as bi
    publish = MagicMock(return_value={"ok": False, "message_id": None, "error": "erro X"})
    with patch("execution.integrations.channel_publisher.publish_to_channel", publish):
        with pytest.raises(RuntimeError, match="erro X"):
            await bi.deliver_message("m", False, reporter, bus, log)
    reporter.finish.assert_not_awaited()


@pytest.mark.asyncio
async def test_telegram_mode_dry_run_returns_false(monkeypatch, mocks):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "telegram")
    reporter, bus, log = mocks
    import execution.scripts.baltic_ingestion as bi
    publish = MagicMock()
    with patch("execution.integrations.channel_publisher.publish_to_channel", publish):
        sent = await bi.deliver_message("m", True, reporter, bus, log)
    assert sent is False
    publish.assert_not_called()


@pytest.mark.asyncio
async def test_uazapi_mode_no_contacts_returns_false(monkeypatch, mocks):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "uazapi")
    # Legacy branch instantiates UazapiClient() unconditionally (mirrors the
    # original script's order, verbatim) before checking for contacts, so it
    # needs a token present even though no message is ever sent here.
    monkeypatch.setenv("UAZAPI_TOKEN", "test-token")
    reporter, bus, log = mocks
    import execution.scripts.baltic_ingestion as bi
    publish = MagicMock()
    repo = MagicMock()
    repo.return_value.list_by_list_code.return_value = []
    with patch("execution.integrations.channel_publisher.publish_to_channel", publish), \
         patch.object(bi, "ContactsRepo", repo):
        sent = await bi.deliver_message("m", False, reporter, bus, log)
    assert sent is False
    publish.assert_not_called()
    reporter.finish.assert_awaited_once()
