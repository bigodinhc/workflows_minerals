"""
Reports navigation helpers for the Telegram bot.

Provides /reports command UI: type selection → latest / year → month → list.
Also provides handle_report_download() for the report_dl callback.
"""

import logging
import os

import requests

logger = logging.getLogger(__name__)

# ── Supabase client (own instance, no circular import with app.py) ──

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


# ── Portuguese month names ──

PT_MONTHS = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
    5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}


# ── Navigation helpers ──

def _reports_show_types(chat_id, message_id=None):
    """Show report type selection (Market Reports / Research Reports)."""
    from telegram import send_telegram_message, edit_message

    text = "📊 *Platts Reports*\n\nEscolha a categoria:"
    markup = {
        "inline_keyboard": [
            [{"text": "📊 Market Reports", "callback_data": "rpt_type:Market Reports"}],
            [{"text": "📊 Research Reports", "callback_data": "rpt_type:Research Reports"}],
        ]
    }
    if message_id:
        edit_message(chat_id, message_id, text, reply_markup=markup)
    else:
        send_telegram_message(chat_id, text, reply_markup=markup)


def _reports_show_latest(chat_id, message_id, report_type):
    """Show the 10 most recent reports of a given type."""
    from telegram import edit_message

    sb = _get_supabase()
    if not sb:
        edit_message(chat_id, message_id, "⚠️ Supabase não configurado")
        return
    try:
        result = sb.table("platts_reports") \
            .select("id, report_name, date_key, frequency") \
            .eq("report_type", report_type) \
            .order("date_key", desc=True) \
            .limit(10) \
            .execute()
        rows = result.data or []
    except Exception as exc:
        logger.error(f"reports latest query error: {exc}")
        edit_message(chat_id, message_id, "⚠️ Erro ao consultar relatórios")
        return

    if not rows:
        keyboard = [[{"text": "⬅ Voltar", "callback_data": "rpt_back:types"}]]
        edit_message(chat_id, message_id, "Nenhum relatório encontrado.", reply_markup={"inline_keyboard": keyboard})
        return

    esc = lambda s: str(s).replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`")
    text = f"📊 *{esc(report_type)}*\n\nÚltimos relatórios:"
    keyboard = []
    for r in rows:
        dk = r["date_key"]
        label = f"{esc(r['report_name'])} — {dk}"
        keyboard.append([{"text": label, "callback_data": f"report_dl:{r['id']}"}])
    keyboard.append([
        {"text": "📅 Ver por data", "callback_data": f"rpt_years:{report_type}"},
        {"text": "⬅ Voltar", "callback_data": "rpt_back:types"},
    ])
    edit_message(chat_id, message_id, text, reply_markup={"inline_keyboard": keyboard})


def _reports_show_years(chat_id, message_id, report_type):
    """Show available years for a report type."""
    from telegram import edit_message

    sb = _get_supabase()
    if not sb:
        edit_message(chat_id, message_id, "⚠️ Supabase não configurado")
        return
    try:
        result = sb.table("platts_reports") \
            .select("date_key") \
            .eq("report_type", report_type) \
            .execute()
        years = sorted({int(r["date_key"][:4]) for r in (result.data or [])}, reverse=True)
    except Exception as exc:
        logger.error(f"reports years query error: {exc}")
        edit_message(chat_id, message_id, "⚠️ Erro ao consultar anos")
        return

    esc = lambda s: str(s).replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`")
    text = f"📊 *{esc(report_type)}*\n\nEscolha o ano:"
    keyboard = [[{"text": str(y), "callback_data": f"rpt_year:{report_type}:{y}"}] for y in years]
    keyboard.append([{"text": "⬅ Voltar", "callback_data": f"rpt_type:{report_type}"}])
    edit_message(chat_id, message_id, text, reply_markup={"inline_keyboard": keyboard})


def _reports_show_months(chat_id, message_id, report_type, year):
    """Show available months for a report type + year, with counts."""
    from telegram import edit_message

    sb = _get_supabase()
    if not sb:
        edit_message(chat_id, message_id, "⚠️ Supabase não configurado")
        return
    try:
        result = sb.table("platts_reports") \
            .select("date_key") \
            .eq("report_type", report_type) \
            .gte("date_key", f"{year}-01-01") \
            .lte("date_key", f"{year}-12-31") \
            .execute()
        month_counts = {}
        for r in (result.data or []):
            m = int(r["date_key"][5:7])
            month_counts[m] = month_counts.get(m, 0) + 1
        months_sorted = sorted(month_counts.items(), reverse=True)
    except Exception as exc:
        logger.error(f"reports months query error: {exc}")
        edit_message(chat_id, message_id, "⚠️ Erro ao consultar meses")
        return

    esc = lambda s: str(s).replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`")
    text = f"📊 *{esc(report_type)} — {year}*\n\nEscolha o mês:"
    keyboard = []
    for m, cnt in months_sorted:
        label = f"{PT_MONTHS.get(m, str(m))} ({cnt})"
        keyboard.append([{"text": label, "callback_data": f"rpt_month:{report_type}:{year}:{m}"}])
    keyboard.append([{"text": "⬅ Voltar", "callback_data": f"rpt_years:{report_type}"}])
    edit_message(chat_id, message_id, text, reply_markup={"inline_keyboard": keyboard})


def _reports_show_month_list(chat_id, message_id, report_type, year, month):
    """Show all reports for a given type + year + month."""
    from telegram import edit_message

    sb = _get_supabase()
    if not sb:
        edit_message(chat_id, message_id, "⚠️ Supabase não configurado")
        return
    try:
        start = f"{year}-{month:02d}-01"
        if month == 12:
            end = f"{year + 1}-01-01"
        else:
            end = f"{year}-{month + 1:02d}-01"
        result = sb.table("platts_reports") \
            .select("id, report_name, date_key") \
            .eq("report_type", report_type) \
            .gte("date_key", start) \
            .lt("date_key", end) \
            .order("date_key", desc=True) \
            .order("report_name") \
            .execute()
        rows = result.data or []
    except Exception as exc:
        logger.error(f"reports month list query error: {exc}")
        edit_message(chat_id, message_id, "⚠️ Erro ao consultar relatórios do mês")
        return

    esc = lambda s: str(s).replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`")
    month_name = PT_MONTHS.get(month, str(month))
    text = f"📊 *{esc(report_type)} — {month_name} {year}*"
    if not rows:
        text += "\n\nNenhum relatório nesse período."
    keyboard = []
    for r in rows:
        day = r["date_key"][8:10]
        label = f"{esc(r['report_name'])} — {day}/{month:02d}"
        keyboard.append([{"text": label, "callback_data": f"report_dl:{r['id']}"}])
    keyboard.append([{"text": "⬅ Voltar", "callback_data": f"rpt_year:{report_type}:{year}"}])
    edit_message(chat_id, message_id, text, reply_markup={"inline_keyboard": keyboard})


# ── Download handler ──

def handle_report_download(chat_id, callback_id, report_id):
    """Download a PDF report from Supabase and send as Telegram document.

    Returns (ok: bool, message: str).
    """
    sb = _get_supabase()
    if not sb:
        return False, "Supabase não configurado"
    try:
        row = sb.table("platts_reports").select("storage_path, report_name").eq("id", report_id).single().execute()
        if not row.data:
            return False, "Relatório não encontrado"
        storage_path = row.data["storage_path"]
        report_name = row.data["report_name"]
        signed = sb.storage.from_("platts-reports").create_signed_url(storage_path, 3600)
        if not signed or not signed.get("signedURL"):
            return False, "Erro ao gerar link"
        pdf_url = signed["signedURL"]
        pdf_resp = requests.get(pdf_url, timeout=30)
        pdf_resp.raise_for_status()
        filename = storage_path.split("/")[-1]
        from telegram import TELEGRAM_BOT_TOKEN
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument",
            data={"chat_id": chat_id, "caption": f"📄 {report_name}", "parse_mode": "Markdown"},
            files={"document": (filename, pdf_resp.content, "application/pdf")},
            timeout=30,
        )
        if not resp.json().get("ok"):
            logger.warning(f"sendDocument failed: {resp.text[:200]}")
        return True, report_name
    except Exception as exc:
        logger.error(f"report_dl error: {exc}")
        return False, "Erro ao baixar relatório"
