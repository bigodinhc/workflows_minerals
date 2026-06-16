"""Tests for run_pipeline_and_archive: archive must hit Supabase, not Redis."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_run_pipeline_and_archive_writes_to_supabase(mocker):
    from bot.routers import _helpers

    # process_news succeeds (draft posting deps stubbed away)
    mocker.patch.object(_helpers, "process_news", new=AsyncMock())

    set_status = mocker.patch(
        "execution.curation.news_repo.set_status", return_value=True
    )
    discard = mocker.patch("execution.curation.redis_client.discard")

    await _helpers.run_pipeline_and_archive(
        chat_id=999, raw_text="body", progress_msg_id=1, item_id="abc123"
    )

    set_status.assert_called_once()
    args, kwargs = set_status.call_args
    assert args[0] == "abc123"
    assert args[1] == "archived"
    assert kwargs.get("chat_id") == 999
    discard.assert_called_once_with("abc123")


@pytest.mark.asyncio
async def test_run_pipeline_and_archive_skips_archive_on_pipeline_failure(mocker):
    from bot.routers import _helpers

    mocker.patch.object(
        _helpers, "process_news", new=AsyncMock(side_effect=RuntimeError("boom"))
    )
    mocker.patch.object(_helpers, "get_bot", return_value=AsyncMock())
    set_status = mocker.patch("execution.curation.news_repo.set_status")
    discard = mocker.patch("execution.curation.redis_client.discard")

    await _helpers.run_pipeline_and_archive(
        chat_id=1, raw_text="body", progress_msg_id=1, item_id="abc123"
    )

    set_status.assert_not_called()
    discard.assert_not_called()
