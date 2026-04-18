"""Callback handlers for report navigation.

Extracted from webhook/bot/routers/callbacks.py during Phase 2 router split.
"""
from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import CallbackQuery

from bot.callback_data import (
    ReportType, ReportYear, ReportMonth, ReportDownload, ReportBack, ReportYears,
)
from bot.middlewares.auth import RoleMiddleware
from reports_nav import (
    reports_show_types, reports_show_latest, reports_show_years,
    reports_show_months, reports_show_month_list, handle_report_download,
)

logger = logging.getLogger(__name__)

callbacks_reports_router = Router(name="callbacks_reports")
callbacks_reports_router.callback_query.middleware(RoleMiddleware(allowed_roles={"admin"}))


# ── Report navigation ──

@callbacks_reports_router.callback_query(ReportType.filter())
async def on_report_type(query: CallbackQuery, callback_data: ReportType):
    await query.answer("")
    await reports_show_latest(query.message.chat.id, query.message.message_id, callback_data.report_type)


@callbacks_reports_router.callback_query(ReportYears.filter())
async def on_report_years(query: CallbackQuery, callback_data: ReportYears):
    await query.answer("")
    await reports_show_years(query.message.chat.id, query.message.message_id, callback_data.report_type)


@callbacks_reports_router.callback_query(ReportYear.filter())
async def on_report_year(query: CallbackQuery, callback_data: ReportYear):
    await query.answer("")
    await reports_show_months(query.message.chat.id, query.message.message_id, callback_data.report_type, callback_data.year)


@callbacks_reports_router.callback_query(ReportMonth.filter())
async def on_report_month(query: CallbackQuery, callback_data: ReportMonth):
    await query.answer("")
    await reports_show_month_list(
        query.message.chat.id, query.message.message_id,
        callback_data.report_type, callback_data.year, callback_data.month,
    )


@callbacks_reports_router.callback_query(ReportDownload.filter())
async def on_report_download(query: CallbackQuery, callback_data: ReportDownload):
    ok, msg = await handle_report_download(query.message.chat.id, query.id, callback_data.report_id)
    await query.answer(f"📤 {msg}" if ok else msg)


@callbacks_reports_router.callback_query(ReportBack.filter())
async def on_report_back(query: CallbackQuery, callback_data: ReportBack):
    await query.answer("")
    chat_id = query.message.chat.id
    message_id = query.message.message_id
    target = callback_data.target
    if target == "types":
        await reports_show_types(chat_id, message_id=message_id)
    elif target.startswith("type:"):
        report_type = target[len("type:"):]
        await reports_show_latest(chat_id, message_id, report_type)
    elif target.startswith("years:"):
        report_type = target[len("years:"):]
        await reports_show_years(chat_id, message_id, report_type)
    elif target.startswith("year:"):
        parts = target[len("year:"):].rsplit(":", 1)
        if len(parts) == 2:
            await reports_show_months(chat_id, message_id, parts[0], int(parts[1]))
