"""Reports navigation helpers for the Telegram bot.

Provides /reports command UI: type selection -> latest / year -> month -> list.
Also provides handle_report_download() for the report_dl callback.

Supabase calls are sync — wrapped with asyncio.to_thread().
"""

from __future__ import annotations

import asyncio
import logging
import os

from bot.config import get_bot
from bot.callback_data import (
    ReportType as ReportTypeCB, ReportYear, ReportMonth,
    ReportDownload, ReportBack, ReportYears,
)

logger = logging.getLogger(__name__)

# ── Supabase client (sync, own instance) ──

_supabase_client = None


def _get_supabase():
    global _supabase_client
    if _supabase_client is None:
        sb_url = os.environ.get("SUPABASE_URL")
        sb_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        if not sb_url or not sb_key:
            logger.warning("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set")
            return None
        from supabase import create_client
        _supabase_client = create_client(sb_url, sb_key)
    return _supabase_client


PT_MONTHS = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
    5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}

_esc = lambda s: str(s).replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`")


async def reports_show_types(chat_id, message_id=None):
    """Show report type selection."""
    from bot.keyboards import build_report_types_keyboard
    bot = get_bot()
    text = "📊 *Platts Reports*\n\nEscolha a categoria:"
    markup = build_report_types_keyboard()
    if message_id:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=markup)
    else:
        await bot.send_message(chat_id, text, reply_markup=markup)


def _query_latest_sync(report_type):
    sb = _get_supabase()
    if not sb:
        return None
    return sb.table("platts_reports") \
        .select("id, report_name, date_key, frequency") \
        .eq("report_type", report_type) \
        .order("date_key", desc=True) \
        .limit(10) \
        .execute()


async def reports_show_latest(chat_id, message_id, report_type):
    """Show the 10 most recent reports of a given type."""
    bot = get_bot()
    try:
        result = await asyncio.to_thread(_query_latest_sync, report_type)
    except Exception as exc:
        logger.error(f"reports latest query error: {exc}")
        await bot.edit_message_text("⚠️ Erro ao consultar relatórios", chat_id=chat_id, message_id=message_id)
        return

    if result is None:
        await bot.edit_message_text("⚠️ Supabase não configurado", chat_id=chat_id, message_id=message_id)
        return

    rows = result.data or []
    if not rows:
        keyboard = {"inline_keyboard": [[{"text": "⬅ Voltar", "callback_data": ReportBack(target="types").pack()}]]}
        await bot.edit_message_text("Nenhum relatório encontrado.", chat_id=chat_id, message_id=message_id, reply_markup=keyboard)
        return

    text = f"📊 *{_esc(report_type)}*\n\nÚltimos relatórios:"
    keyboard = []
    for r in rows:
        dk = r["date_key"]
        label = f"{_esc(r['report_name'])} — {dk}"
        keyboard.append([{"text": label, "callback_data": ReportDownload(report_id=str(r["id"])).pack()}])
    keyboard.append([
        {"text": "📅 Ver por data", "callback_data": ReportYears(report_type=report_type).pack()},
        {"text": "⬅ Voltar", "callback_data": ReportBack(target="types").pack()},
    ])
    await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup={"inline_keyboard": keyboard})


def _query_years_sync(report_type):
    sb = _get_supabase()
    if not sb:
        return None
    return sb.table("platts_reports").select("date_key").eq("report_type", report_type).execute()


async def reports_show_years(chat_id, message_id, report_type):
    """Show available years for a report type."""
    bot = get_bot()
    try:
        result = await asyncio.to_thread(_query_years_sync, report_type)
    except Exception as exc:
        logger.error(f"reports years query error: {exc}")
        await bot.edit_message_text("⚠️ Erro ao consultar anos", chat_id=chat_id, message_id=message_id)
        return

    if result is None:
        await bot.edit_message_text("⚠️ Supabase não configurado", chat_id=chat_id, message_id=message_id)
        return

    years = sorted({int(r["date_key"][:4]) for r in (result.data or [])}, reverse=True)
    text = f"📊 *{_esc(report_type)}*\n\nEscolha o ano:"
    keyboard = [[{"text": str(y), "callback_data": ReportYear(report_type=report_type, year=y).pack()}] for y in years]
    keyboard.append([{"text": "⬅ Voltar", "callback_data": ReportTypeCB(report_type=report_type).pack()}])
    await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup={"inline_keyboard": keyboard})


def _query_months_sync(report_type, year):
    sb = _get_supabase()
    if not sb:
        return None
    return sb.table("platts_reports") \
        .select("date_key") \
        .eq("report_type", report_type) \
        .gte("date_key", f"{year}-01-01") \
        .lte("date_key", f"{year}-12-31") \
        .execute()


async def reports_show_months(chat_id, message_id, report_type, year):
    """Show available months for a report type + year."""
    bot = get_bot()
    try:
        result = await asyncio.to_thread(_query_months_sync, report_type, year)
    except Exception as exc:
        logger.error(f"reports months query error: {exc}")
        await bot.edit_message_text("⚠️ Erro ao consultar meses", chat_id=chat_id, message_id=message_id)
        return

    if result is None:
        await bot.edit_message_text("⚠️ Supabase não configurado", chat_id=chat_id, message_id=message_id)
        return

    month_counts = {}
    for r in (result.data or []):
        m = int(r["date_key"][5:7])
        month_counts[m] = month_counts.get(m, 0) + 1
    months_sorted = sorted(month_counts.items(), reverse=True)

    text = f"📊 *{_esc(report_type)} — {year}*\n\nEscolha o mês:"
    keyboard = []
    for m, cnt in months_sorted:
        label = f"{PT_MONTHS.get(m, str(m))} ({cnt})"
        keyboard.append([{"text": label, "callback_data": ReportMonth(report_type=report_type, year=year, month=m).pack()}])
    keyboard.append([{"text": "⬅ Voltar", "callback_data": ReportYears(report_type=report_type).pack()}])
    await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup={"inline_keyboard": keyboard})


def _query_month_list_sync(report_type, year, month):
    sb = _get_supabase()
    if not sb:
        return None
    start = f"{year}-{month:02d}-01"
    end = f"{year + 1}-01-01" if month == 12 else f"{year}-{month + 1:02d}-01"
    return sb.table("platts_reports") \
        .select("id, report_name, date_key") \
        .eq("report_type", report_type) \
        .gte("date_key", start) \
        .lt("date_key", end) \
        .order("date_key", desc=True) \
        .order("report_name") \
        .execute()


async def reports_show_month_list(chat_id, message_id, report_type, year, month):
    """Show all reports for a given type + year + month."""
    bot = get_bot()
    try:
        result = await asyncio.to_thread(_query_month_list_sync, report_type, year, month)
    except Exception as exc:
        logger.error(f"reports month list query error: {exc}")
        await bot.edit_message_text("⚠️ Erro ao consultar relatórios do mês", chat_id=chat_id, message_id=message_id)
        return

    if result is None:
        await bot.edit_message_text("⚠️ Supabase não configurado", chat_id=chat_id, message_id=message_id)
        return

    rows = result.data or []
    month_name = PT_MONTHS.get(month, str(month))
    text = f"📊 *{_esc(report_type)} — {month_name} {year}*"
    if not rows:
        text += "\n\nNenhum relatório nesse período."

    keyboard = []
    for r in rows:
        day = r["date_key"][8:10]
        label = f"{_esc(r['report_name'])} — {day}/{month:02d}"
        keyboard.append([{"text": label, "callback_data": ReportDownload(report_id=str(r["id"])).pack()}])
    keyboard.append([{"text": "⬅ Voltar", "callback_data": ReportYear(report_type=report_type, year=year).pack()}])
    await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup={"inline_keyboard": keyboard})


def _download_report_sync(report_id):
    """Sync: query Supabase for report metadata + signed URL + PDF bytes."""
    import requests
    sb = _get_supabase()
    if not sb:
        return None, "Supabase não configurado"
    row = sb.table("platts_reports").select("storage_path, report_name").eq("id", report_id).single().execute()
    if not row.data:
        return None, "Relatório não encontrado"
    storage_path = row.data["storage_path"]
    report_name = row.data["report_name"]
    signed = sb.storage.from_("platts-reports").create_signed_url(storage_path, 3600)
    if not signed or not signed.get("signedURL"):
        return None, "Erro ao gerar link"
    pdf_url = signed["signedURL"]
    pdf_resp = requests.get(pdf_url, timeout=30)
    pdf_resp.raise_for_status()
    filename = storage_path.split("/")[-1]
    return {"content": pdf_resp.content, "filename": filename, "report_name": report_name}, None


async def handle_report_download(chat_id, callback_id, report_id):
    """Download a PDF report from Supabase and send as Telegram document.

    Returns (ok: bool, message: str).
    """
    try:
        result, error = await asyncio.to_thread(_download_report_sync, report_id)
    except Exception as exc:
        logger.error(f"report_dl error: {exc}")
        return False, "Erro ao baixar relatório"

    if result is None:
        return False, error

    bot = get_bot()
    from aiogram.types import BufferedInputFile
    doc = BufferedInputFile(result["content"], filename=result["filename"])
    await bot.send_document(chat_id, doc, caption=f"📄 {result['report_name']}")
    return True, result["report_name"]
