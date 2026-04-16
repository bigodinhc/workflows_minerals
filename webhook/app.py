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
import anthropic
from datetime import datetime, timezone, timedelta
from pathlib import Path
from flask import Flask, request, jsonify

_HERE = Path(__file__).resolve().parent
# Railway: /app/execution/ lives alongside app.py after Docker COPY
sys.path.insert(0, str(_HERE))
# Local dev: <repo>/execution/ is sibling to webhook/
sys.path.insert(0, str(_HERE.parent))
from execution.core.delivery_reporter import DeliveryReporter, Contact, build_contact_from_row
import contact_admin
import query_handlers
import redis_queries
from execution.integrations.sheets_client import SheetsClient
from execution.core.prompts import WRITER_SYSTEM, CRITIQUE_SYSTEM, CURATOR_SYSTEM, ADJUSTER_SYSTEM
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

# Config from environment
UAZAPI_URL = os.getenv("UAZAPI_URL", "https://mineralstrading.uazapi.com")
UAZAPI_TOKEN = (os.getenv("UAZAPI_TOKEN") or "").strip()
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
ANTHROPIC_API_KEY = (os.getenv("ANTHROPIC_API_KEY") or "").strip()

# Google Sheets for contacts
SHEET_ID = "1tU3Izdo21JichTXg15bc1paWUiN8XioJYZUPpbIUgL0"

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

# AI AGENT PROMPTS: imported from execution.core.prompts
# (Writer, Critique, Curator, Adjuster — see execution/core/prompts/*.py)

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
# GOOGLE SHEETS (contacts)
# ============================================================

def get_contacts():
    """Fetch WhatsApp contacts from Google Sheets."""
    import gspread
    from google.oauth2.service_account import Credentials
    import time

    creds_json = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(creds_json, scopes=[
        "https://www.googleapis.com/auth/spreadsheets.readonly"
    ])
    gc = gspread.authorize(creds)
    sheet = gc.open_by_key(SHEET_ID).sheet1
    
    # Retry logic to handle intermittent Google API 500 errors
    max_retries = 3
    records = []
    for attempt in range(max_retries):
        try:
            records = sheet.get_all_records()
            break
        except Exception as e:
            if attempt == max_retries - 1:
                logger.error(f"Failed to fetch contacts after {max_retries} attempts: {e}")
                raise
            sleep_time = 2 ** attempt
            logger.warning(f"Google Sheets API error {e}. Retrying in {sleep_time}s...")
            time.sleep(sleep_time)

    contacts = [r for r in records if r.get("ButtonPayload") == "Big"]
    logger.info(f"Found {len(contacts)} contacts with ButtonPayload='Big'")
    return contacts

# ============================================================
# WHATSAPP SENDING
# ============================================================

def send_whatsapp(phone, message, token=None, url=None):
    """Send WhatsApp message via Uazapi."""
    use_token = token or UAZAPI_TOKEN
    use_url = url or UAZAPI_URL
    headers = {
        "token": use_token,
        "Content-Type": "application/json"
    }
    payload = {
        "number": str(phone),
        "text": message
    }
    try:
        response = requests.post(
            f"{use_url}/send/text",
            json=payload,
            headers=headers,
            timeout=30
        )
        if response.status_code != 200:
            logger.error(f"WhatsApp {phone}: HTTP {response.status_code} - {response.text[:200]}")
        return response.status_code == 200
    except Exception as e:
        logger.error(f"WhatsApp send error for {phone}: {e}")
        return False

# ============================================================
# AI PROCESSING (3-agent chain)
# ============================================================

def call_claude(system_prompt, user_prompt):
    """Call Claude API and return text response."""
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )
        return message.content[0].text
    except anthropic.APIConnectionError as e:
        logger.error(f"Anthropic connection error: {e}")
        raise
    except anthropic.AuthenticationError as e:
        logger.error(f"Anthropic auth error (bad key?): {e}")
        raise
    except Exception as e:
        logger.error(f"Anthropic error ({type(e).__name__}): {e}")
        raise

def run_3_agents(raw_text, on_phase_start=None):
    """Run Writer → Critique → Curator chain. Returns final formatted message.

    on_phase_start: optional callable(phase_name) invoked imediatamente
    antes de cada fase. Usado para atualizar a mensagem de progresso no
    Telegram (edit_message). Nomes passados: "Writer", "Reviewer",
    "Finalizer" — nomes user-facing (não coincidem com os prompts
    internos WRITER_SYSTEM/CRITIQUE_SYSTEM/CURATOR_SYSTEM, intencional).
    """
    if on_phase_start:
        on_phase_start("Writer")
    logger.info("Agent 1/3: Writer starting...")
    writer_output = call_claude(
        WRITER_SYSTEM,
        f"Processe e analise o seguinte conteúdo do mercado de minério de ferro.\n\nCONTEÚDO:\n---\n{raw_text}\n---\n\nProduza sua análise completa."
    )
    logger.info(f"Writer done ({len(writer_output)} chars)")

    if on_phase_start:
        on_phase_start("Reviewer")
    logger.info("Agent 2/3: Critique starting...")
    critique_output = call_claude(
        CRITIQUE_SYSTEM,
        f"Revise o trabalho do Writer:\n\nTRABALHO DO WRITER:\n---\n{writer_output}\n---\n\nTEXTO ORIGINAL:\n---\n{raw_text}\n---\n\nExecute sua revisão crítica."
    )
    logger.info(f"Critique done ({len(critique_output)} chars)")

    if on_phase_start:
        on_phase_start("Finalizer")
    logger.info("Agent 3/3: Curator starting...")
    curator_output = call_claude(
        CURATOR_SYSTEM,
        f"Crie a versão final para WhatsApp.\n\nTEXTO DO WRITER:\n---\n{writer_output}\n---\n\nFEEDBACK DO CRITIQUE:\n---\n{critique_output}\n---\n\nTEXTO ORIGINAL:\n---\n{raw_text}\n---\n\nProduza APENAS a mensagem formatada."
    )
    logger.info(f"Curator done ({len(curator_output)} chars)")

    return curator_output

def run_adjuster(current_draft, feedback, original_text):
    """Re-run Curator with adjustment feedback."""
    logger.info("Adjuster starting...")
    adjusted = call_claude(
        ADJUSTER_SYSTEM,
        f"MENSAGEM ATUAL:\n---\n{current_draft}\n---\n\nAJUSTES SOLICITADOS:\n---\n{feedback}\n---\n\nTEXTO ORIGINAL (referência):\n---\n{original_text}\n---\n\nAplique os ajustes e produza a mensagem final."
    )
    logger.info(f"Adjuster done ({len(adjusted)} chars)")
    return adjusted

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

def _send_whatsapp_raising(phone, text, token=None, url=None):
    """Raising wrapper around send_whatsapp for DeliveryReporter contract."""
    use_token = token or UAZAPI_TOKEN
    use_url = url or UAZAPI_URL
    headers = {"token": use_token, "Content-Type": "application/json"}
    payload = {"number": str(phone), "text": text}
    response = requests.post(
        f"{use_url}/send/text",
        json=payload,
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def process_approval_async(chat_id, draft_message, uazapi_token=None, uazapi_url=None):
    """Process WhatsApp sending with progress updates via DeliveryReporter."""
    progress = send_telegram_message(chat_id, "⏳ Iniciando envio para WhatsApp...")
    progress_msg_id = progress.get("result", {}).get("message_id") if progress.get("ok") else None

    try:
        raw_contacts = get_contacts()

        delivery_contacts = [bc for c in raw_contacts if (bc := build_contact_from_row(c))]

        if progress_msg_id:
            edit_message(chat_id, progress_msg_id,
                f"⏳ Enviando para {len(delivery_contacts)} contatos...\n0/{len(delivery_contacts)}")

        def on_progress(processed, total_, result):
            if progress_msg_id and processed % 10 == 0:
                edit_message(
                    chat_id,
                    progress_msg_id,
                    f"⏳ Enviando...\n{processed}/{total_} processados",
                )

        def send_fn(phone, text):
            _send_whatsapp_raising(phone, text, token=uazapi_token, url=uazapi_url)

        reporter = DeliveryReporter(
            workflow="webhook_approval",
            send_fn=send_fn,
            telegram_chat_id=chat_id,
            gh_run_id=None,
        )
        report = reporter.dispatch(delivery_contacts, draft_message, on_progress=on_progress)

        if progress_msg_id:
            edit_message(
                chat_id,
                progress_msg_id,
                f"✔️ Envio finalizado — veja resumo detalhado abaixo.",
            )

        logger.info(
            f"Approval complete: {report.success_count} sent, {report.failure_count} failed"
        )

    except Exception as e:
        logger.error(f"Approval processing error: {e}")
        error_text = f"❌ ERRO NO ENVIO\n\n{str(e)}"
        if progress_msg_id:
            edit_message(chat_id, progress_msg_id, error_text)
        else:
            send_telegram_message(chat_id, error_text)

def process_test_send_async(chat_id, draft_id, draft_message, uazapi_token=None, uazapi_url=None):
    """Send message only to the first contact for testing."""
    try:
        contacts = get_contacts()
        if not contacts:
            send_telegram_message(chat_id, "❌ Nenhum contato encontrado na planilha.")
            return
        
        first_contact = contacts[0]
        name = first_contact.get("Nome", "Contato 1")
        phone = first_contact.get("Evolution-api") or first_contact.get("Telefone")
        if not phone:
            send_telegram_message(chat_id, "❌ Primeiro contato sem telefone.")
            return
        
        phone = str(phone).replace("whatsapp:", "").strip()
        
        if send_whatsapp(phone, draft_message, token=uazapi_token, url=uazapi_url):
            send_telegram_message(chat_id, 
                f"🧪 *TESTE OK*\n\n"
                f"✅ Enviado para: {name} ({phone})\n\n"
                f"Se ficou bom, clique em ✅ Aprovar para enviar a todos os {len(contacts)} contatos.")
            # Re-send approval buttons
            send_approval_message(chat_id, draft_id, draft_message)
        else:
            send_telegram_message(chat_id, 
                f"❌ *TESTE FALHOU*\n\n"
                f"Falha ao enviar para: {name} ({phone})\n"
                f"Verifique o token UAZAPI.")
            
        logger.info(f"Test send for {draft_id}: {name} ({phone})")
    except Exception as e:
        logger.error(f"Test send error: {e}")
        send_telegram_message(chat_id, f"❌ Erro no teste:\n{str(e)[:500]}")

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
                {"text": "❓ Help", "callback_data": "menu:help"},
            ],
        ]
    }
    send_telegram_message(chat_id, text, reply_markup=markup)


# ── Reports navigation helpers ──

PT_MONTHS = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
    5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}

def _reports_show_types(chat_id, message_id=None):
    """Show report type selection (Market Reports / Research Reports)."""
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
    sb = get_supabase()
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
    sb = get_supabase()
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
    sb = get_supabase()
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
    sb = get_supabase()
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


def handle_callback(callback_query):
    """Handle button press callbacks."""
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
        parts_dl = callback_data.split(":", 1)
        report_id = parts_dl[1] if len(parts_dl) > 1 else ""
        sb = get_supabase()
        if not sb:
            answer_callback(callback_id, "Supabase não configurado")
            return jsonify({"ok": True})
        try:
            row = sb.table("platts_reports").select("storage_path, report_name").eq("id", report_id).single().execute()
            if not row.data:
                answer_callback(callback_id, "Relatório não encontrado")
                return jsonify({"ok": True})
            storage_path = row.data["storage_path"]
            report_name = row.data["report_name"]
            signed = sb.storage.from_("platts-reports").create_signed_url(storage_path, 3600)
            if not signed or not signed.get("signedURL"):
                answer_callback(callback_id, "Erro ao gerar link")
                return jsonify({"ok": True})
            pdf_url = signed["signedURL"]
            pdf_resp = requests.get(pdf_url, timeout=30)
            pdf_resp.raise_for_status()
            filename = storage_path.split("/")[-1]
            # Direct multipart upload (telegram_api sends JSON, can't do files)
            resp = requests.post(
                f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendDocument",
                data={"chat_id": chat_id, "caption": f"📄 {report_name}", "parse_mode": "Markdown"},
                files={"document": (filename, pdf_resp.content, "application/pdf")},
                timeout=30,
            )
            if not resp.json().get("ok"):
                logger.warning(f"sendDocument failed: {resp.text[:200]}")
            answer_callback(callback_id, f"📤 {report_name}")
        except Exception as exc:
            logger.error(f"report_dl error: {exc}")
            answer_callback(callback_id, "Erro ao baixar relatório")
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
            args=(chat_id, draft["message"], draft.get("uazapi_token"), draft.get("uazapi_url"))
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
            args=(chat_id, draft_id, draft["message"], draft.get("uazapi_token"), draft.get("uazapi_url"))
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
            "awaiting_feedback": True
        }

        answer_callback(callback_id, "✏️ Modo ajuste")
        finalize_card(
            chat_id,
            callback_query,
            "✏️ *Em modo ajuste* — envie o feedback na próxima mensagem",
        )
        send_telegram_message(chat_id,
            "✏️ *MODO AJUSTE*\n\n"
            "Envie uma mensagem descrevendo o que quer ajustar.\n\n"
            "Exemplos:\n"
            "• _Remova o terceiro parágrafo_\n"
            "• _Adicione que o preço subiu 2%_\n"
            "• _Resuma em menos linhas_\n"
            "• _Mude o título para X_")
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
            f"Title: {item.get('title','')}\n"
            f"Date: {item.get('publishDate','')}\n"
            f"Source: {item.get('source','')}\n\n"
            f"{item.get('fullText','')}"
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
