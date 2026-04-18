"""Characterization tests — report navigation callbacks in webhook/bot/routers/callbacks.py."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from bot.callback_data import (
    ReportType, ReportYears, ReportYear, ReportMonth, ReportDownload, ReportBack,
)
from bot.routers.callbacks import (
    on_report_type, on_report_years, on_report_year, on_report_month,
    on_report_download, on_report_back,
)


@pytest.mark.asyncio
async def test_on_report_type_delegates_to_reports_show_latest(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100, message_id=200)
    show_latest = mocker.patch("bot.routers.callbacks.reports_show_latest", new=AsyncMock())

    await on_report_type(query, ReportType(report_type="rmw"))

    show_latest.assert_awaited_once_with(100, 200, "rmw")
    query.answer.assert_awaited_with("")


@pytest.mark.asyncio
async def test_on_report_years_delegates_to_reports_show_years(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100, message_id=200)
    show_years = mocker.patch("bot.routers.callbacks.reports_show_years", new=AsyncMock())

    await on_report_years(query, ReportYears(report_type="rmw"))

    show_years.assert_awaited_once_with(100, 200, "rmw")


@pytest.mark.asyncio
async def test_on_report_year_delegates_to_reports_show_months(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100, message_id=200)
    show_months = mocker.patch("bot.routers.callbacks.reports_show_months", new=AsyncMock())

    await on_report_year(query, ReportYear(report_type="rmw", year=2026))

    show_months.assert_awaited_once_with(100, 200, "rmw", 2026)


@pytest.mark.asyncio
async def test_on_report_month_delegates_to_reports_show_month_list(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100, message_id=200)
    show_list = mocker.patch("bot.routers.callbacks.reports_show_month_list", new=AsyncMock())

    await on_report_month(query, ReportMonth(report_type="rmw", year=2026, month=4))

    show_list.assert_awaited_once_with(100, 200, "rmw", 2026, 4)


@pytest.mark.asyncio
async def test_on_report_download_success_answers_with_upload_emoji(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100)
    mocker.patch(
        "bot.routers.callbacks.handle_report_download",
        new=AsyncMock(return_value=(True, "enviado")),
    )

    await on_report_download(query, ReportDownload(report_id="rep_abc"))

    query.answer.assert_awaited_with("📤 enviado")


@pytest.mark.asyncio
async def test_on_report_download_failure_answers_raw_message(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100)
    mocker.patch(
        "bot.routers.callbacks.handle_report_download",
        new=AsyncMock(return_value=(False, "arquivo não encontrado")),
    )

    await on_report_download(query, ReportDownload(report_id="rep_missing"))

    query.answer.assert_awaited_with("arquivo não encontrado")


@pytest.mark.asyncio
async def test_on_report_back_types_routes_to_show_types(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100, message_id=200)
    show_types = mocker.patch("bot.routers.callbacks.reports_show_types", new=AsyncMock())

    await on_report_back(query, ReportBack(target="types"))

    show_types.assert_awaited_once_with(100, message_id=200)


@pytest.mark.asyncio
async def test_on_report_back_year_target_parses_type_and_year(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100, message_id=200)
    show_months = mocker.patch("bot.routers.callbacks.reports_show_months", new=AsyncMock())

    await on_report_back(query, ReportBack(target="year:rmw:2026"))

    show_months.assert_awaited_once_with(100, 200, "rmw", 2026)
