"""
Telegram Webhook Server for Minerals Trading
Handles:
1. Rationale News approval (from GitHub Actions)
2. Manual news dispatch (text → 3 AI agents → approve/adjust/reject → WhatsApp)
Deploy to Railway.
"""

import os
import sys
import json
import logging
import threading
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from flask import Flask, request, jsonify

_HERE = Path(__file__).resolve().parent
# Railway: /app/execution/ lives alongside app.py after Docker COPY
sys.path.insert(0, str(_HERE))
# Local dev: <repo>/execution/ is sibling to webhook/
sys.path.insert(0, str(_HERE.parent))
import contact_admin
import query_handlers
import redis_queries
from execution.integrations.sheets_client import SheetsClient
from dispatch import (
    get_contacts,
    send_whatsapp,
    process_approval_async,
    process_test_send_async,
    UAZAPI_URL,
    UAZAPI_TOKEN,
    SHEET_ID,
    GOOGLE_CREDENTIALS_JSON,
)
from pipeline import call_claude, run_3_agents, run_adjuster, ANTHROPIC_API_KEY
from telegram import (
    telegram_api,
    answer_callback,
    send_telegram_message,
    edit_message,
    finalize_card,
    send_approval_message,
    TELEGRAM_BOT_TOKEN,
)
from status_builder import build_status_message, ALL_WORKFLOWS, _format_status_lines
from reports_nav import (
    _reports_show_types,
    _reports_show_latest,
    _reports_show_years,
    _reports_show_months,
    _reports_show_month_list,
    handle_report_download,
)
from callback_router import handle_callback

# Supabase client for report downloads
_supabase_client = None

def get_supabase():
    global _supabase_client
    if _supabase_client is None:
        sb_url = os.environ.get("SUPABASE_URL")
        sb_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        if not sb_url or not sb_key:
            logger.warning("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set — report downloads disabled")
            return None
        from supabase import create_client
        _supabase_client = create_client(sb_url, sb_key)
    return _supabase_client

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# In-memory state (ADJUST_STATE + SEEN_ARTICLES are ephemeral; DRAFTS now in Redis)
ADJUST_STATE = {}   # chat_id → {draft_id, awaiting_feedback: True}
SEEN_ARTICLES = {}  # date_str → set of article titles (for market_news dedup)
REJECT_REASON_STATE: dict = {}   # chat_id → {feedback_key, expires_at}
REJECT_REASON_TIMEOUT_SECONDS = 120


def begin_reject_reason(chat_id: int, action: str, item_id: str, title: str) -> str:
    """Save a placeholder feedback entry and set state to await a reason message.

    Returns the feedback_key so callers can display it if useful.
    """
    import time
    feedback_key = redis_queries.save_feedback(
        action=action, item_id=item_id, chat_id=chat_id, reason="", title=title or "",
    )
    REJECT_REASON_STATE[chat_id] = {
        "feedback_key": feedback_key,
        "expires_at": time.time() + REJECT_REASON_TIMEOUT_SECONDS,
    }
    return feedback_key


def consume_reject_reason(chat_id: int, text: str):
    """Consume the next user message as the rejection reason.

    Returns:
        ('saved', reason_text)  if a reason was saved
        ('skipped', '')         if the user typed 'pular' or 'skip'
        None                    if there is no pending state or it expired
    """
    import time
    state = REJECT_REASON_STATE.get(chat_id)
    if state is None:
        return None
    if time.time() >= state.get("expires_at", 0):
        REJECT_REASON_STATE.pop(chat_id, None)
        return None
    feedback_key = state["feedback_key"]
    stripped = (text or "").strip()
    if stripped.lower() in {"pular", "skip"}:
        REJECT_REASON_STATE.pop(chat_id, None)
        return ("skipped", "")
    redis_queries.update_feedback_reason(feedback_key, stripped)
    REJECT_REASON_STATE.pop(chat_id, None)
    return ("saved", stripped)


# ── Persistent drafts store (Redis, 7d TTL) ──
# Replaces the in-memory DRAFTS dict so drafts survive Railway redeploys.
_DRAFT_KEY_PREFIX = "webhook:draft:"
_DRAFT_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days


def _drafts_client():
    """Return Redis client used for draft persistence (same keyspace helper as curation)."""
    from execution.curation.redis_client import _get_client
    return _get_client()


def drafts_get(draft_id):
    """Return draft dict or None if missing/unreachable."""
    try:
        raw = _drafts_client().get(f"{_DRAFT_KEY_PREFIX}{draft_id}")
        if raw:
            return json.loads(raw)
    except Exception as exc:
        logger.warning(f"drafts_get({draft_id}) failed: {exc}")
    return None


def drafts_set(draft_id, draft):
    """Persist draft with 7d TTL. Logs but does not raise on Redis failure."""
    try:
        _drafts_client().set(
            f"{_DRAFT_KEY_PREFIX}{draft_id}",
            json.dumps(draft),
            ex=_DRAFT_TTL_SECONDS,
        )
    except Exception as exc:
        logger.error(f"drafts_set({draft_id}) failed: {exc}")


def drafts_contains(draft_id):
    try:
        return bool(_drafts_client().exists(f"{_DRAFT_KEY_PREFIX}{draft_id}"))
    except Exception as exc:
        logger.warning(f"drafts_contains({draft_id}) failed: {exc}")
        return False


def drafts_update(draft_id, **fields):
    """Read-modify-write for partial field updates."""
    draft = drafts_get(draft_id)
    if draft is None:
        return
    draft.update(fields)
    drafts_set(draft_id, draft)

# Log config at startup
logger.info(f"UAZAPI_URL: {UAZAPI_URL}")
logger.info(f"UAZAPI_TOKEN: {'SET (' + UAZAPI_TOKEN[:8] + '...)' if UAZAPI_TOKEN else 'NOT SET'}")
logger.info(f"TELEGRAM_BOT_TOKEN: {'SET' if TELEGRAM_BOT_TOKEN else 'NOT SET'}")
logger.info(f"ANTHROPIC_API_KEY: {'SET' if ANTHROPIC_API_KEY else 'NOT SET'}")

_telegram_chat_id_env = os.getenv("TELEGRAM_CHAT_ID", "").strip()
if _telegram_chat_id_env:
    _masked = _telegram_chat_id_env[:3] + "***" + _telegram_chat_id_env[-2:] if len(_telegram_chat_id_env) > 6 else "***"
    logger.info(f"TELEGRAM_CHAT_ID: SET ({_masked})")
else:
    logger.info("TELEGRAM_CHAT_ID: NOT SET (admin commands will silently fail)")

def _render_list_view(chat_id, page, search, message_id=None):
    """Fetch contacts and render list message with keyboard.
    If message_id is None → sends new message.
    Otherwise → edits existing message."""
    try:
        sheets = SheetsClient()
        per_page = 10
        contacts, total_pages = sheets.list_contacts(
            SHEET_ID, search=search, page=page, per_page=per_page,
        )
        all_contacts, _ = sheets.list_contacts(
            SHEET_ID, search=search, page=1, per_page=10_000,
        )
        total = len(all_contacts)

        msg = contact_admin.render_list_message(
            contacts, total=total, page=page, per_page=per_page, search=search,
        )
        kb = contact_admin.build_list_keyboard(
            contacts, page=page, total_pages=total_pages, search=search,
        )

        if message_id is None:
            send_telegram_message(chat_id, msg, reply_markup=kb)
        else:
            edit_message(chat_id, message_id, msg, reply_markup=kb)
    except Exception as e:
        logger.error(f"_render_list_view failed: {e}")
        err_msg = "❌ Erro ao acessar planilha. Tente novamente."
        if message_id:
            edit_message(chat_id, message_id, err_msg)
        else:
            send_telegram_message(chat_id, err_msg)


def _handle_add_data(chat_id, text):
    """Process the user's 'Nome Telefone' message after /add prompt."""
    try:
        name, phone = contact_admin.parse_add_input(text)
    except ValueError as e:
        send_telegram_message(chat_id, f"❌ {e}")
        return  # keep state so user can retry

    try:
        sheets = SheetsClient()
        sheets.add_contact(SHEET_ID, name, phone)
    except ValueError as e:
        send_telegram_message(chat_id, f"❌ {e}")
        contact_admin.clear_state(chat_id)
        return
    except Exception as e:
        logger.error(f"add_contact failed: {e}")
        send_telegram_message(chat_id, "❌ Erro ao gravar na planilha. Tente novamente.")
        contact_admin.clear_state(chat_id)
        return

    try:
        sheets = SheetsClient()
        all_contacts, _ = sheets.list_contacts(SHEET_ID, page=1, per_page=10_000)
        active = sum(1 for c in all_contacts if str(c.get("ButtonPayload", "")).strip() == "Big")
    except Exception:
        active = "?"

    send_telegram_message(chat_id, f"✅ {name} adicionado\nTotal ativos: {active}")
    contact_admin.clear_state(chat_id)


# ============================================================
# ASYNC PROCESSING
# ============================================================

def process_news_async(chat_id, raw_text, progress_msg_id):
    """Process news text through 3 agents in background thread.

    Edits `progress_msg_id` in-place via agents_progress.format_pipeline_progress
    to show current phase (Writer → Reviewer → Finalizer → Draft pronto).
    """
    from execution.core.agents_progress import format_pipeline_progress
    phase_order = ["Writer", "Reviewer", "Finalizer"]
    done: list = []

    def hook(phase_name):
        # Mark all earlier phases as done before rendering
        idx = phase_order.index(phase_name)
        done.clear()
        done.extend(phase_order[:idx])
        if progress_msg_id:
            edit_message(chat_id, progress_msg_id, format_pipeline_progress(
                current=phase_name, done=list(done),
            ))

    try:
        # Initial state shown BEFORE the Writer actually starts
        if progress_msg_id:
            edit_message(chat_id, progress_msg_id, format_pipeline_progress(
                current="Writer", done=[],
            ))

        final_message = run_3_agents(raw_text, on_phase_start=hook)

        # Store draft
        import time
        draft_id = f"news_{int(time.time())}"
        drafts_set(draft_id, {
            "message": final_message,
            "status": "pending",
            "original_text": raw_text,
            "uazapi_token": None,
            "uazapi_url": None,
        })

        # Final state: all phases done
        if progress_msg_id:
            edit_message(chat_id, progress_msg_id, format_pipeline_progress(
                current=None, done=list(phase_order),
            ))
        send_approval_message(chat_id, draft_id, final_message)

    except Exception as e:
        logger.error(f"process_news_async failed: {e}")
        if progress_msg_id:
            # Show error on the first phase NOT in done (i.e. the one that failed)
            remaining = [p for p in phase_order if p not in done]
            current = remaining[0] if remaining else None
            edit_message(chat_id, progress_msg_id, format_pipeline_progress(
                current=current, done=list(done), error=str(e)[:120],
            ))

def process_adjustment_async(chat_id, draft_id, feedback):
    """Adjust draft with user feedback in background thread."""
    progress = send_telegram_message(chat_id, "⏳ Ajustando mensagem...")
    progress_msg_id = progress.get("result", {}).get("message_id") if progress.get("ok") else None
    
    try:
        draft = drafts_get(draft_id)
        if not draft:
            send_telegram_message(chat_id, "❌ Draft não encontrado.")
            return

        adjusted = run_adjuster(draft["message"], feedback, draft["original_text"])

        # Update draft (persist back to Redis)
        draft["message"] = adjusted
        draft["status"] = "pending"
        drafts_set(draft_id, draft)

        if progress_msg_id:
            edit_message(chat_id, progress_msg_id, "✅ Ajuste concluído!")
        
        send_approval_message(chat_id, draft_id, adjusted)
        logger.info(f"Draft {draft_id} adjusted")
    except Exception as e:
        logger.error(f"Adjustment error: {e}")
        if progress_msg_id:
            edit_message(chat_id, progress_msg_id, f"❌ Erro no ajuste:\n{str(e)[:500]}")

# ============================================================
# ROUTES
# ============================================================

@app.route("/preview/<item_id>", methods=["GET"])
def preview_item(item_id):
    """Render Platts item HTML preview for Telegram in-app browser.

    Looks up item in Redis staging first, then in today's and yesterday's
    archive (covers post-midnight opens), then returns a 404 HTML message
    if missing/expired.
    """
    from datetime import datetime, timedelta, timezone
    from flask import render_template
    from execution.curation import redis_client

    item = None
    try:
        item = redis_client.get_staging(item_id)
    except Exception as exc:
        logger.warning(f"Preview staging lookup failed: {exc}")

    if item is None:
        now_utc = datetime.now(timezone.utc)
        for offset in (0, 1):
            date = (now_utc - timedelta(days=offset)).strftime("%Y-%m-%d")
            try:
                item = redis_client.get_archive(date, item_id)
            except Exception as exc:
                logger.warning(f"Preview archive lookup failed ({date}): {exc}")
                continue
            if item is not None:
                break

    if item is None:
        return (
            "<!DOCTYPE html><html lang='pt-BR'><head><meta charset='UTF-8'>"
            "<title>Item não encontrado</title></head><body>"
            "<h1>Item não encontrado</h1>"
            "<p>Expirou (48h) ou já foi processado.</p>"
            "</body></html>",
            404,
        )

    # Defensive coercion — a malformed scraper payload shouldn't crash the template
    safe_item = dict(item)
    if not isinstance(safe_item.get("fullText"), str):
        safe_item["fullText"] = ""
    if not isinstance(safe_item.get("tables"), list):
        safe_item["tables"] = []

    return render_template("preview.html", item=safe_item)


@app.route("/health", methods=["GET"])
def health():
    # drafts_count is approximate — SCAN could be slow with many keys, so we skip it
    return jsonify({
        "status": "ok",
        "seen_articles_dates": len(SEEN_ARTICLES),
        "uazapi_token_set": bool(UAZAPI_TOKEN),
        "uazapi_url": UAZAPI_URL,
        "anthropic_key_set": bool(ANTHROPIC_API_KEY),
        "anthropic_key_prefix": ANTHROPIC_API_KEY[:10] + "..." if ANTHROPIC_API_KEY else "NONE"
    })

@app.route("/test-ai", methods=["GET"])
def test_ai():
    """Test Anthropic API connectivity from Railway."""
    if not ANTHROPIC_API_KEY:
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500
    try:
        result = call_claude("You are helpful.", "Say 'hello' in one word.")
        return jsonify({"status": "ok", "response": result[:100]})
    except Exception as e:
        return jsonify({"status": "error", "error_type": type(e).__name__, "error": str(e)[:500]}), 500

@app.route("/store-draft", methods=["POST"])
def store_draft():
    """Store a draft for later approval. Called by GitHub Actions."""
    data = request.json
    draft_id = data.get("draft_id")
    message = data.get("message")
    
    if not draft_id or not message:
        return jsonify({"error": "Missing draft_id or message"}), 400
    
    draft = {
        "message": message,
        "status": "pending",
        "original_text": "",
        "uazapi_token": (data.get("uazapi_token") or "").strip() or None,
        "uazapi_url": (data.get("uazapi_url") or "").strip() or None
    }
    drafts_set(draft_id, draft)

    if draft["uazapi_token"]:
        logger.info(f"Draft includes UAZAPI token: {draft['uazapi_token'][:8]}...")
    else:
        logger.info(f"Draft has no UAZAPI token, will use env var")
    
    logger.info(f"Draft stored: {draft_id} ({len(message)} chars)")
    return jsonify({"success": True, "draft_id": draft_id})

@app.route("/seen-articles", methods=["GET"])
def get_seen_articles():
    """Return list of seen article titles for a given date (dedup for market_news)."""
    date = request.args.get("date", "")
    if not date:
        return jsonify({"error": "Missing 'date' query parameter"}), 400
    titles = list(SEEN_ARTICLES.get(date, set()))
    return jsonify({"date": date, "titles": titles})

@app.route("/seen-articles", methods=["POST"])
def store_seen_articles():
    """Store new article titles and prune entries older than 3 days."""
    from datetime import datetime, timedelta
    data = request.json
    date = data.get("date", "")
    titles = data.get("titles", [])

    if not date or not titles:
        return jsonify({"error": "Missing 'date' or 'titles'"}), 400

    if date not in SEEN_ARTICLES:
        SEEN_ARTICLES[date] = set()
    SEEN_ARTICLES[date].update(titles)

    # Prune entries older than 3 days
    try:
        cutoff = datetime.now() - timedelta(days=3)
        stale_keys = [
            k for k in SEEN_ARTICLES
            if datetime.strptime(k, "%Y-%m-%d") < cutoff
        ]
        for k in stale_keys:
            del SEEN_ARTICLES[k]
    except ValueError as e:
        logger.warning(f"Date format mismatch during seen-articles pruning: {e}")

    logger.info(f"Stored {len(titles)} seen articles for {date} (total: {len(SEEN_ARTICLES.get(date, []))})")
    return jsonify({"success": True, "stored": len(titles)})

@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    """Handle all Telegram updates: text messages AND callback queries."""
    update = request.json
    logger.info(f"Webhook received update_id: {update.get('update_id')}")
    
    # ── Handle callback query (button press) ──
    callback_query = update.get("callback_query")
    if callback_query:
        return handle_callback(callback_query)
    
    # ── Handle text message ──
    message = update.get("message", {})
    text = message.get("text", "")
    chat_id = message.get("chat", {}).get("id")
    
    if not text or not chat_id:
        return jsonify({"ok": True})
    
    # Bot commands
    if text.startswith("/"):
        # Any new command cancels in-progress /add
        if contact_admin.is_awaiting_add(chat_id):
            contact_admin.clear_state(chat_id)

        if text == "/start":
            send_telegram_message(chat_id,
                "👋 *Minerals Trading Bot*\n\n"
                "*Notícias:*\n"
                "Cole texto — viro relatório via IA e envio pra aprovação.\n\n"
                "*Contatos (admin):*\n"
                "`/status` — status dos workflows\n"
                "`/add` — adicionar contato\n"
                "`/list [busca]` — listar e ativar/desativar\n"
                "`/cancel` — desistir do /add em curso")
            return jsonify({"ok": True})

        if text == "/status":
            if not contact_admin.is_authorized(chat_id):
                logger.warning(f"/status rejected: chat_id={chat_id} not authorized")
                return jsonify({"ok": True})
            try:
                body = build_status_message()
            except Exception as exc:
                logger.error(f"/status failed: {exc}")
                body = f"⚠️ Erro ao gerar status: {str(exc)[:100]}"
            send_telegram_message(chat_id, body)
            return jsonify({"ok": True})

        if text == "/cancel":
            if contact_admin.is_authorized(chat_id):
                send_telegram_message(chat_id, "Cancelado.")
            return jsonify({"ok": True})

        if text == "/add":
            if not contact_admin.is_authorized(chat_id):
                logger.warning(f"/add rejected: chat_id={chat_id} not in TELEGRAM_CHAT_ID env")
                return jsonify({"ok": True})  # silent ignore
            contact_admin.start_add_flow(chat_id)
            send_telegram_message(chat_id, contact_admin.render_add_prompt())
            return jsonify({"ok": True})

        if text.startswith("/list"):
            if not contact_admin.is_authorized(chat_id):
                logger.warning(f"/list rejected: chat_id={chat_id} not in TELEGRAM_CHAT_ID env")
                return jsonify({"ok": True})
            parts = text.split(None, 1)
            search = parts[1].strip() if len(parts) > 1 else None
            _render_list_view(chat_id, page=1, search=search, message_id=None)
            return jsonify({"ok": True})

        if text.startswith("/reprocess"):
            if not contact_admin.is_authorized(chat_id):
                return jsonify({"ok": True})
            parts = text.split(None, 1)
            if len(parts) < 2 or not parts[1].strip():
                send_telegram_message(
                    chat_id,
                    "Uso: `/reprocess <item_id>`\n\n"
                    "O item_id é o `🆔` mostrado no rodapé dos cards de curadoria.\n"
                    "Busca em staging (48h) e depois em archive (7d).",
                )
                return jsonify({"ok": True})
            _reprocess_item(chat_id, parts[1].strip())
            return jsonify({"ok": True})

        if text == "/help":
            if not contact_admin.is_authorized(chat_id):
                logger.warning(f"/help rejected: chat_id={chat_id} not authorized")
                return jsonify({"ok": True})
            send_telegram_message(chat_id, query_handlers.format_help())
            return jsonify({"ok": True})

        if text == "/history":
            if not contact_admin.is_authorized(chat_id):
                logger.warning(f"/history rejected: chat_id={chat_id} not authorized")
                return jsonify({"ok": True})
            try:
                body = query_handlers.format_history()
            except Exception as exc:
                logger.error(f"/history error: {exc}")
                send_telegram_message(chat_id, "❌ Erro ao consultar arquivo.")
                return jsonify({"ok": True})
            send_telegram_message(chat_id, body)
            return jsonify({"ok": True})

        if text == "/stats":
            if not contact_admin.is_authorized(chat_id):
                logger.warning(f"/stats rejected: chat_id={chat_id} not authorized")
                return jsonify({"ok": True})
            today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            try:
                body = query_handlers.format_stats(today_iso)
            except Exception as exc:
                logger.error(f"/stats error: {exc}")
                send_telegram_message(chat_id, "❌ Erro ao calcular stats.")
                return jsonify({"ok": True})
            send_telegram_message(chat_id, body)
            return jsonify({"ok": True})

        if text == "/rejections":
            if not contact_admin.is_authorized(chat_id):
                logger.warning(f"/rejections rejected: chat_id={chat_id} not authorized")
                return jsonify({"ok": True})
            try:
                body = query_handlers.format_rejections()
            except Exception as exc:
                logger.error(f"/rejections error: {exc}")
                send_telegram_message(chat_id, "❌ Erro ao listar recusas.")
                return jsonify({"ok": True})
            send_telegram_message(chat_id, body)
            return jsonify({"ok": True})

        if text == "/queue":
            if not contact_admin.is_authorized(chat_id):
                logger.warning(f"/queue rejected: chat_id={chat_id} not authorized")
                return jsonify({"ok": True})
            try:
                body, markup = query_handlers.format_queue_page(page=1)
            except Exception as exc:
                logger.error(f"/queue error: {exc}")
                send_telegram_message(chat_id, "❌ Erro ao consultar staging.")
                return jsonify({"ok": True})
            send_telegram_message(chat_id, body, reply_markup=markup)
            return jsonify({"ok": True})

        if text == "/reports":
            if not contact_admin.is_authorized(chat_id):
                return jsonify({"ok": True})
            _reports_show_types(chat_id)
            return jsonify({"ok": True})

        if text == "/workflows":
            if not contact_admin.is_authorized(chat_id):
                return jsonify({"ok": True})
            from workflow_trigger import render_workflow_list
            wf_text, wf_markup = render_workflow_list()
            send_telegram_message(chat_id, wf_text, reply_markup=wf_markup)
            return jsonify({"ok": True})

        if text == "/s":
            if not contact_admin.is_authorized(chat_id):
                return jsonify({"ok": True})
            _show_main_menu(chat_id)
            return jsonify({"ok": True})

        return jsonify({"ok": True})  # unknown command
    
    # ── Check if user is in admin add flow ──
    if contact_admin.is_awaiting_add(chat_id):
        if not contact_admin.is_authorized(chat_id):
            contact_admin.clear_state(chat_id)
            return jsonify({"ok": True})
        _handle_add_data(chat_id, text)
        return jsonify({"ok": True})

    # ── Check if user is in adjustment mode ──
    adjust = ADJUST_STATE.get(chat_id)
    if adjust and adjust.get("awaiting_feedback"):
        draft_id = adjust["draft_id"]
        del ADJUST_STATE[chat_id]
        
        logger.info(f"Received adjustment feedback for {draft_id}")
        
        thread = threading.Thread(
            target=process_adjustment_async,
            args=(chat_id, draft_id, text)
        )
        thread.daemon = True
        thread.start()
        return jsonify({"ok": True})

    # ── Check if user is responding to a rejection-reason prompt ──
    reject_result = consume_reject_reason(chat_id, text)
    if reject_result is not None:
        status, reason = reject_result
        if status == "saved":
            send_telegram_message(chat_id, "✅ Razão registrada.")
        else:
            send_telegram_message(chat_id, "✅ Ok, sem razão registrada.")
        return jsonify({"ok": True})

    # ── New news text: process with 3 agents ──
    if not ANTHROPIC_API_KEY:
        send_telegram_message(chat_id, "❌ ANTHROPIC_API_KEY não configurada no servidor.")
        return jsonify({"ok": True})
    
    logger.info(f"New news text from chat {chat_id} ({len(text)} chars)")
    
    # Send processing indicator
    progress = send_telegram_message(chat_id, "⏳ Processando sua notícia com 3 agentes IA...")
    progress_msg_id = progress.get("result", {}).get("message_id") if progress.get("ok") else None
    
    if progress_msg_id:
        thread = threading.Thread(
            target=process_news_async,
            args=(chat_id, text, progress_msg_id)
        )
        thread.daemon = True
        thread.start()
    
    return jsonify({"ok": True})

def _run_pipeline_and_archive(chat_id, raw_text, progress_msg_id, item_id):
    """Wrap process_news_async so staging is only drained on success.

    If the pipeline raises, the staging item remains (48h TTL) so the
    curator can retry. Archive happens only after run_3_agents + webhook
    dispatch completed cleanly.
    """
    from execution.curation import redis_client
    try:
        process_news_async(chat_id, raw_text, progress_msg_id)
    except Exception as exc:
        logger.error(f"pipeline failed for {item_id}: {exc}")
        return
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        redis_client.archive(item_id, date, chat_id=chat_id)
    except Exception as exc:
        logger.warning(f"archive post-success failed for {item_id}: {exc}")


def _find_curation_item(item_id):
    """Look up a Platts curation item by id in staging → today/yesterday archive.

    Returns the item dict or None if not found anywhere.
    """
    from execution.curation import redis_client
    try:
        item = redis_client.get_staging(item_id)
    except Exception as exc:
        logger.warning(f"reprocess staging lookup failed for {item_id}: {exc}")
        item = None
    if item is not None:
        return item
    now_utc = datetime.now(timezone.utc)
    for offset in (0, 1):
        date = (now_utc - timedelta(days=offset)).strftime("%Y-%m-%d")
        try:
            item = redis_client.get_archive(date, item_id)
        except Exception as exc:
            logger.warning(f"reprocess archive lookup failed ({date}, {item_id}): {exc}")
            continue
        if item is not None:
            return item
    return None


def _reprocess_item(chat_id, item_id):
    """Re-run the 3-agent pipeline on a curation item pulled from Redis.

    Looks up the item in staging → today/yesterday archive, then feeds its
    raw text into the same pipeline used by `curate_pipeline`. This lets the
    admin recover items whose buttons have already been consumed (e.g. when
    a previous click hit a bug or the draft was lost on redeploy).
    """
    item = _find_curation_item(item_id)
    if item is None:
        send_telegram_message(
            chat_id,
            f"❌ Item `{item_id}` não encontrado em staging nem archive recente.",
        )
        return
    raw_text = (
        f"Title: {item.get('title','')}\n"
        f"Date: {item.get('publishDate','')}\n"
        f"Source: {item.get('source','')}\n\n"
        f"{item.get('fullText','')}"
    )
    progress = send_telegram_message(
        chat_id,
        f"🖋️ *Reprocessando via Writer*\n🆔 `{item_id}`",
    )
    progress_msg_id = progress.get("result", {}).get("message_id") if progress else None
    threading.Thread(
        target=_run_pipeline_and_archive,
        args=(chat_id, raw_text, progress_msg_id, item_id),
        daemon=True,
    ).start()


# ── Menu helpers ──

def _safe_text(fn, label):
    """Call fn(), return its result or an error string."""
    try:
        return fn()
    except Exception as exc:
        logger.error(f"menu {label} error: {exc}")
        return f"⚠️ Erro ao consultar {label}."

def _safe_call(fn, label):
    """Call fn() expecting (text, markup) tuple. Return (error_text, None) on failure."""
    try:
        return fn()
    except Exception as exc:
        logger.error(f"menu {label} error: {exc}")
        return (f"⚠️ Erro ao consultar {label}.", None)


# ── Main menu ──

def _show_main_menu(chat_id):
    """Send a main menu with inline buttons for all bot features."""
    text = "🥸 *SuperMustache BOT*"
    markup = {
        "inline_keyboard": [
            [
                {"text": "📊 Relatórios", "callback_data": "menu:reports"},
                {"text": "📰 Fila", "callback_data": "menu:queue"},
            ],
            [
                {"text": "📜 Histórico", "callback_data": "menu:history"},
                {"text": "❌ Recusados", "callback_data": "menu:rejections"},
            ],
            [
                {"text": "📈 Stats", "callback_data": "menu:stats"},
                {"text": "🔄 Status", "callback_data": "menu:status"},
            ],
            [
                {"text": "🔁 Reprocessar", "callback_data": "menu:reprocess"},
                {"text": "📋 Contatos", "callback_data": "menu:list"},
            ],
            [
                {"text": "➕ Add Contato", "callback_data": "menu:add"},
            ],
            [
                {"text": "⚡ Workflows", "callback_data": "wf_list"},
                {"text": "❓ Help", "callback_data": "menu:help"},
            ],
        ]
    }
    send_telegram_message(chat_id, text, reply_markup=markup)




@app.route("/admin/register-commands", methods=["POST"])
def register_commands():
    """Register bot commands with Telegram's setMyCommands so they appear
    in the / autocomplete menu. Call manually (e.g. via curl) once after
    deploy or whenever the command list changes.

    Auth: chat_id query param must belong to an authorized admin.
    """
    raw_chat_id = request.args.get("chat_id", "")
    try:
        chat_id = int(raw_chat_id)
    except ValueError:
        return jsonify({"ok": False, "error": "chat_id query param required"}), 400
    if not contact_admin.is_authorized(chat_id):
        return jsonify({"ok": False, "error": "unauthorized"}), 403

    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return jsonify({"ok": False, "error": "TELEGRAM_BOT_TOKEN missing"}), 500

    commands = [
        {"command": "s", "description": "Menu principal com todos os atalhos"},
        {"command": "workflows", "description": "Disparar workflows (GitHub Actions)"},
        {"command": "reports", "description": "Consultar e baixar relatórios Platts (PDF)"},
        {"command": "help", "description": "Lista todos os comandos"},
        {"command": "queue", "description": "Items aguardando curadoria"},
        {"command": "history", "description": "Ultimos 10 arquivados"},
        {"command": "rejections", "description": "Ultimas 10 recusas"},
        {"command": "stats", "description": "Contadores de hoje"},
        {"command": "status", "description": "Saude dos workflows"},
        {"command": "reprocess", "description": "Re-dispara pipeline num item"},
        {"command": "add", "description": "Adicionar contato"},
        {"command": "list", "description": "Listar contatos"},
        {"command": "cancel", "description": "Abortar fluxo atual"},
    ]
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/setMyCommands",
            json={"commands": commands},
            timeout=10,
        )
        data = resp.json()
    except Exception as exc:
        logger.error(f"setMyCommands request failed: {exc}")
        return jsonify({"ok": False, "error": str(exc)}), 502
    if not data.get("ok"):
        logger.error(f"setMyCommands returned not-ok: {data}")
        return jsonify({"ok": False, "telegram": data}), 502
    logger.info(f"setMyCommands registered {len(commands)} commands")
    return jsonify({"ok": True, "registered": len(commands)})


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
