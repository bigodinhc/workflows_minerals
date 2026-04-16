"""
callback_router.py — Telegram button-press callback dispatcher.

All inline-keyboard callbacks from the /webhook route are handled here.
Extracted from app.py to keep the main module at a manageable size.

Circular-import note: this module needs several helpers that still live in
app.py (drafts_*, ADJUST_STATE, begin_reject_reason, …). To avoid a module-
level circular import we import them lazily *inside* handle_callback, which is
only called at request time — long after all modules have been fully loaded.
"""

import os
import logging
import threading
from datetime import datetime, timezone

from flask import jsonify

import contact_admin
import query_handlers
import redis_queries
from telegram import (
    answer_callback,
    send_telegram_message,
    edit_message,
    finalize_card,
    send_approval_message,
)
from dispatch import (
    process_approval_async,
    process_test_send_async,
    SHEET_ID,
)
from reports_nav import (
    _reports_show_types,
    _reports_show_latest,
    _reports_show_years,
    _reports_show_months,
    _reports_show_month_list,
    handle_report_download,
)
from status_builder import build_status_message
from execution.integrations.sheets_client import SheetsClient

logger = logging.getLogger(__name__)


def handle_callback(callback_query):
    """Handle button press callbacks."""
    # Lazy import to avoid circular dependency at module load time
    from app import (
        drafts_get,
        drafts_contains,
        drafts_update,
        drafts_set,
        ADJUST_STATE,
        begin_reject_reason,
        _show_main_menu,
        _render_list_view,
        _safe_text,
        _safe_call,
        _run_pipeline_and_archive,
        process_news_async,
        process_adjustment_async,
    )
    from pipeline import run_adjuster

    callback_id = callback_query["id"]
    callback_data = callback_query.get("data", "")
    chat_id = callback_query["message"]["chat"]["id"]

    logger.info(f"Callback: {callback_data} from chat {chat_id}")

    # Contact admin callbacks
    if callback_data == "nop":
        answer_callback(callback_id, "")
        return jsonify({"ok": True})

    if callback_data.startswith("tgl:"):
        if not contact_admin.is_authorized(chat_id):
            answer_callback(callback_id, "Não autorizado")
            return jsonify({"ok": True})
        phone = callback_data[4:]
        try:
            sheets = SheetsClient()
            name, new_status = sheets.toggle_contact(SHEET_ID, phone)
        except ValueError as e:
            answer_callback(callback_id, f"❌ {str(e)[:100]}")
            return jsonify({"ok": True})
        except Exception as e:
            logger.error(f"toggle_contact failed: {e}")
            answer_callback(callback_id, "❌ Erro")
            return jsonify({"ok": True})

        toast = f"✅ {name} ativado" if new_status == "Big" else f"❌ {name} desativado"
        answer_callback(callback_id, toast)

        message_id = callback_query["message"]["message_id"]
        _render_list_view(chat_id, page=1, search=None, message_id=message_id)
        return jsonify({"ok": True})

    if callback_data.startswith("pg:"):
        if not contact_admin.is_authorized(chat_id):
            answer_callback(callback_id, "Não autorizado")
            return jsonify({"ok": True})
        rest = callback_data[3:]
        if ":" in rest:
            page_str, search = rest.split(":", 1)
        else:
            page_str, search = rest, None
        try:
            page = int(page_str)
        except ValueError:
            answer_callback(callback_id, "Página inválida")
            return jsonify({"ok": True})

        answer_callback(callback_id, "")
        message_id = callback_query["message"]["message_id"]
        _render_list_view(chat_id, page=page, search=search, message_id=message_id)
        return jsonify({"ok": True})

    if callback_data.startswith("queue_page:"):
        if not contact_admin.is_authorized(chat_id):
            answer_callback(callback_id, "Não autorizado")
            return jsonify({"ok": True})
        try:
            page = int(callback_data.split(":", 1)[1])
        except ValueError:
            answer_callback(callback_id, "Página inválida")
            return jsonify({"ok": True})
        answer_callback(callback_id, "")
        message_id = callback_query["message"]["message_id"]
        try:
            body, markup = query_handlers.format_queue_page(page=page)
        except Exception as exc:
            logger.error(f"queue_page error: {exc}")
            return jsonify({"ok": True})
        edit_message(chat_id, message_id, body, reply_markup=markup)
        return jsonify({"ok": True})

    if callback_data.startswith("queue_open:"):
        if not contact_admin.is_authorized(chat_id):
            answer_callback(callback_id, "Não autorizado")
            return jsonify({"ok": True})
        item_id = callback_data.split(":", 1)[1]
        from execution.curation import redis_client as curation_redis
        from execution.curation import telegram_poster
        try:
            item = curation_redis.get_staging(item_id)
        except Exception as exc:
            logger.error(f"queue_open redis error: {exc}")
            answer_callback(callback_id, "⚠️ Redis indisponível")
            return jsonify({"ok": True})
        if item is None:
            answer_callback(callback_id, "⚠️ Item expirou")
            return jsonify({"ok": True})
        answer_callback(callback_id, "")
        preview_base_url = os.getenv("TELEGRAM_WEBHOOK_URL", "").rstrip("/")
        try:
            telegram_poster.post_for_curation(chat_id, item, preview_base_url)
        except Exception as exc:
            logger.error(f"queue_open post error: {exc}")
            send_telegram_message(chat_id, "❌ Erro ao abrir card.")
        return jsonify({"ok": True})

    # ---------- Main menu shortcuts ----------
    if callback_data.startswith("menu:"):
        action_menu = callback_data.split(":", 1)[1]
        answer_callback(callback_id, "")
        handlers = {
            "reports": lambda: _reports_show_types(chat_id),
            "queue": lambda: send_telegram_message(chat_id, *_safe_call(lambda: query_handlers.format_queue_page(page=1), "fila")),
            "history": lambda: send_telegram_message(chat_id, _safe_text(lambda: query_handlers.format_history(), "histórico")),
            "rejections": lambda: send_telegram_message(chat_id, _safe_text(lambda: query_handlers.format_rejections(), "recusas")),
            "stats": lambda: send_telegram_message(chat_id, _safe_text(lambda: query_handlers.format_stats(datetime.now(timezone.utc).strftime("%Y-%m-%d")), "stats")),
            "status": lambda: send_telegram_message(chat_id, _safe_text(lambda: build_status_message(), "status")),
            "reprocess": lambda: send_telegram_message(chat_id, "Uso: `/reprocess <item\\_id>`\n\nDigite o comando com o ID do item."),
            "list": lambda: send_telegram_message(chat_id, "Uso: `/list [busca]`\n\nDigite o comando ou `/list` pra ver todos."),
            "add": lambda: send_telegram_message(chat_id, "Uso: `/add`\n\nDigite o comando pra iniciar."),
            "help": lambda: send_telegram_message(chat_id, _safe_text(lambda: query_handlers.format_help(), "help")),
        }
        handler = handlers.get(action_menu)
        if handler:
            handler()
        return jsonify({"ok": True})

    # ---------- Report PDF download ----------
    if callback_data.startswith("report_dl:"):
        report_id = callback_data.split(":", 1)[1]
        ok, msg = handle_report_download(chat_id, callback_id, report_id)
        answer_callback(callback_id, f"📤 {msg}" if ok else msg)
        return jsonify({"ok": True})

    # ---------- Reports navigation ----------
    if callback_data.startswith("rpt_type:"):
        report_type = callback_data.split(":", 1)[1]
        message_id = callback_query["message"]["message_id"]
        _reports_show_latest(chat_id, message_id, report_type)
        answer_callback(callback_id, "")
        return jsonify({"ok": True})

    if callback_data.startswith("rpt_years:"):
        report_type = callback_data.split(":", 1)[1]
        message_id = callback_query["message"]["message_id"]
        _reports_show_years(chat_id, message_id, report_type)
        answer_callback(callback_id, "")
        return jsonify({"ok": True})

    if callback_data.startswith("rpt_year:"):
        _, report_type, year = callback_data.split(":", 2)
        message_id = callback_query["message"]["message_id"]
        _reports_show_months(chat_id, message_id, report_type, int(year))
        answer_callback(callback_id, "")
        return jsonify({"ok": True})

    if callback_data.startswith("rpt_month:"):
        _, report_type, year, month = callback_data.split(":", 3)
        message_id = callback_query["message"]["message_id"]
        _reports_show_month_list(chat_id, message_id, report_type, int(year), int(month))
        answer_callback(callback_id, "")
        return jsonify({"ok": True})

    if callback_data.startswith("rpt_back:"):
        rest = callback_data[len("rpt_back:"):]
        message_id = callback_query["message"]["message_id"]
        if rest == "types":
            _reports_show_types(chat_id, message_id=message_id)
        elif rest.startswith("type:"):
            report_type = rest[len("type:"):]
            _reports_show_latest(chat_id, message_id, report_type)
        elif rest.startswith("years:"):
            report_type = rest[len("years:"):]
            _reports_show_years(chat_id, message_id, report_type)
        elif rest.startswith("year:"):
            parts_back = rest[len("year:"):].rsplit(":", 1)
            if len(parts_back) == 2:
                _reports_show_months(chat_id, message_id, parts_back[0], int(parts_back[1]))
        answer_callback(callback_id, "")
        return jsonify({"ok": True})

    parts = callback_data.split(":", 1)
    if len(parts) != 2:
        answer_callback(callback_id, "Erro: dados inválidos")
        return jsonify({"ok": True})

    action, draft_id = parts

    if action == "approve":
        draft = drafts_get(draft_id)
        if not draft:
            logger.warning(f"Draft not found: {draft_id}")
            answer_callback(callback_id, "❌ Draft não encontrado")
            finalize_card(chat_id, callback_query, "❌ *DRAFT EXPIRADO*\n\nRode o workflow novamente.")
            return jsonify({"ok": True})

        if draft["status"] != "pending":
            answer_callback(callback_id, "⚠️ Já processado")
            finalize_card(
                chat_id,
                callback_query,
                f"⚠️ *Já processado* ({draft['status']})",
            )
            return jsonify({"ok": True})

        drafts_update(draft_id, status="approved")
        answer_callback(callback_id, "✅ Aprovado! Enviando...")
        finalize_card(
            chat_id,
            callback_query,
            f"✅ *Aprovado* em {datetime.now(timezone.utc).strftime('%H:%M')} UTC — envio em andamento",
        )

        thread = threading.Thread(
            target=process_approval_async,
            args=(chat_id, draft["message"], draft.get("uazapi_token"), draft.get("uazapi_url")),
        )
        thread.daemon = True
        thread.start()
        return jsonify({"ok": True})

    elif action == "test_approve":
        draft = drafts_get(draft_id)
        if not draft:
            answer_callback(callback_id, "❌ Draft não encontrado")
            finalize_card(chat_id, callback_query, "❌ *Draft não encontrado*")
            return jsonify({"ok": True})

        answer_callback(callback_id, "🧪 Enviando teste para 1 contato...")
        finalize_card(
            chat_id,
            callback_query,
            f"🧪 *Teste em andamento* — {datetime.now(timezone.utc).strftime('%H:%M')} UTC",
        )

        thread = threading.Thread(
            target=process_test_send_async,
            args=(chat_id, draft_id, draft["message"], draft.get("uazapi_token"), draft.get("uazapi_url")),
        )
        thread.daemon = True
        thread.start()
        return jsonify({"ok": True})

    elif action == "adjust":
        draft = drafts_get(draft_id)
        if not draft:
            answer_callback(callback_id, "❌ Draft não encontrado")
            finalize_card(chat_id, callback_query, "❌ *Draft não encontrado*")
            return jsonify({"ok": True})

        # Set adjustment state
        ADJUST_STATE[chat_id] = {
            "draft_id": draft_id,
            "awaiting_feedback": True,
        }

        answer_callback(callback_id, "✏️ Modo ajuste")
        finalize_card(
            chat_id,
            callback_query,
            "✏️ *Em modo ajuste* — envie o feedback na próxima mensagem",
        )
        send_telegram_message(
            chat_id,
            "✏️ *MODO AJUSTE*\n\n"
            "Envie uma mensagem descrevendo o que quer ajustar.\n\n"
            "Exemplos:\n"
            "• _Remova o terceiro parágrafo_\n"
            "• _Adicione que o preço subiu 2%_\n"
            "• _Resuma em menos linhas_\n"
            "• _Mude o título para X_",
        )
        return jsonify({"ok": True})

    elif action == "reject":
        # Snapshot title before update
        snapshot_title = ""
        draft = drafts_get(draft_id)
        if draft:
            msg = draft.get("message") or ""
            for line in msg.splitlines():
                stripped = line.strip().lstrip("📊").strip()
                if stripped and stripped != "*MINERALS TRADING*":
                    snapshot_title = stripped[:80]
                    break
            if not snapshot_title:
                snapshot_title = f"Draft {draft_id[:8]}"
        else:
            snapshot_title = f"Draft {draft_id[:8]}"

        if drafts_contains(draft_id):
            drafts_update(draft_id, status="rejected")
        try:
            begin_reject_reason(chat_id, "draft_reject", draft_id, snapshot_title)
        except Exception as exc:
            logger.error(f"draft reject begin_reject_reason error: {exc}")
        answer_callback(callback_id, "❌ Rejeitado")
        finalize_card(
            chat_id,
            callback_query,
            f"❌ *Recusado*\n🕒 {datetime.now(timezone.utc).strftime('%H:%M')} UTC\n\n"
            f"💭 Por quê? (opcional — responda ou `pular`)",
        )
        return jsonify({"ok": True})

    elif action == "curate_archive":
        if not contact_admin.is_authorized(chat_id):
            answer_callback(callback_id, "Não autorizado")
            return jsonify({"ok": True})
        item_id = parts[1] if len(parts) > 1 else ""
        from execution.curation import redis_client
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            archived = redis_client.archive(item_id, date, chat_id=chat_id)
        except Exception as exc:
            logger.error(f"curate_archive redis error: {exc}")
            answer_callback(callback_id, "⚠️ Redis indisponível, tenta de novo")
            return jsonify({"ok": True})
        if archived is None:
            answer_callback(callback_id, "⚠️ Item expirou ou já processado")
            finalize_card(chat_id, callback_query, "⚠️ Item expirou ou já processado")
            return jsonify({"ok": True})
        answer_callback(callback_id, "✅ Arquivado")
        finalize_card(
            chat_id,
            callback_query,
            f"✅ *Arquivado*\n🕒 {datetime.now(timezone.utc).strftime('%H:%M')} UTC · 🆔 `{item_id}`",
        )
        return jsonify({"ok": True})

    elif action == "curate_reject":
        if not contact_admin.is_authorized(chat_id):
            answer_callback(callback_id, "Não autorizado")
            return jsonify({"ok": True})
        item_id = parts[1] if len(parts) > 1 else ""
        from execution.curation import redis_client
        # Snapshot title before discard so it survives in feedback
        snapshot_title = ""
        try:
            item = redis_client.get_staging(item_id)
            if item:
                snapshot_title = item.get("title") or ""
        except Exception:
            pass
        try:
            redis_client.discard(item_id)
        except Exception as exc:
            logger.error(f"curate_reject redis error: {exc}")
            answer_callback(callback_id, "⚠️ Redis indisponível")
            return jsonify({"ok": True})
        try:
            begin_reject_reason(chat_id, "curate_reject", item_id, snapshot_title)
        except Exception as exc:
            logger.error(f"curate_reject begin_reject_reason error: {exc}")
        answer_callback(callback_id, "❌ Recusado")
        finalize_card(
            chat_id,
            callback_query,
            f"❌ *Recusado*\n🕒 {datetime.now(timezone.utc).strftime('%H:%M')} UTC · 🆔 `{item_id}`\n\n"
            f"💭 Por quê? (opcional — responda ou `pular`)",
        )
        return jsonify({"ok": True})

    elif action == "curate_pipeline":
        if not contact_admin.is_authorized(chat_id):
            answer_callback(callback_id, "Não autorizado")
            return jsonify({"ok": True})
        item_id = parts[1] if len(parts) > 1 else ""
        from execution.curation import redis_client
        try:
            item = redis_client.get_staging(item_id)
        except Exception as exc:
            logger.error(f"curate_pipeline redis error: {exc}")
            answer_callback(callback_id, "⚠️ Redis indisponível")
            return jsonify({"ok": True})
        if item is None:
            answer_callback(callback_id, "⚠️ Item expirou")
            finalize_card(chat_id, callback_query, "⚠️ Item expirou ou já processado")
            return jsonify({"ok": True})
        try:
            redis_queries.mark_pipeline_processed(item_id, datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        except Exception as exc:
            logger.warning(f"mark_pipeline_processed failed for {item_id}: {exc}")
        raw_text = (
            f"Title: {item.get('title', '')}\n"
            f"Date: {item.get('publishDate', '')}\n"
            f"Source: {item.get('source', '')}\n\n"
            f"{item.get('fullText', '')}"
        )
        answer_callback(callback_id, "🖋️ Enviando para o Writer...")
        progress = send_telegram_message(chat_id, f"🖋️ *Enviando para o Writer*\n🆔 `{item_id}`")
        progress_msg_id = progress.get("result", {}).get("message_id") if progress else None
        # Finalize the original card BEFORE starting the thread so user sees confirmation immediately
        finalize_card(
            chat_id,
            callback_query,
            f"🖋️ *Enviado para o Writer*\n🕒 {datetime.now(timezone.utc).strftime('%H:%M')} UTC · 🆔 `{item_id}`",
        )
        threading.Thread(
            target=_run_pipeline_and_archive,
            args=(chat_id, raw_text, progress_msg_id, item_id),
            daemon=True,
        ).start()
        return jsonify({"ok": True})

    return jsonify({"ok": True})
