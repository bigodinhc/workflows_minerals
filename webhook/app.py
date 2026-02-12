"""
Telegram Webhook Server for Minerals Trading
Handles:
1. Rationale News approval (from GitHub Actions)
2. Manual news dispatch (text → 3 AI agents → approve/adjust/reject → WhatsApp)
Deploy to Railway.
"""

import os
import json
import logging
import threading
import requests
import anthropic
from flask import Flask, request, jsonify

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Config from environment
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
UAZAPI_URL = os.getenv("UAZAPI_URL", "https://mineralstrading.uazapi.com")
UAZAPI_TOKEN = (os.getenv("UAZAPI_TOKEN") or "").strip()
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Google Sheets for contacts
SHEET_ID = "1tU3Izdo21JichTXg15bc1paWUiN8XioJYZUPpbIUgL0"

# In-memory state
DRAFTS = {}         # draft_id → {message, status, original_text, uazapi_token, uazapi_url}
ADJUST_STATE = {}   # chat_id → {draft_id, awaiting_feedback: True}

# Log config at startup
logger.info(f"UAZAPI_URL: {UAZAPI_URL}")
logger.info(f"UAZAPI_TOKEN: {'SET (' + UAZAPI_TOKEN[:8] + '...)' if UAZAPI_TOKEN else 'NOT SET'}")
logger.info(f"TELEGRAM_BOT_TOKEN: {'SET' if TELEGRAM_BOT_TOKEN else 'NOT SET'}")
logger.info(f"ANTHROPIC_API_KEY: {'SET' if ANTHROPIC_API_KEY else 'NOT SET'}")

# ============================================================
# AI AGENT PROMPTS (from n8n workflow)
# ============================================================

WRITER_SYSTEM = """# System Prompt para o Writer

Você é um analista sênior de mercado de minério de ferro da Minerals Trading. Sua especialidade é processar informações brutas do mercado internacional e criar sínteses claras em português brasileiro.

## METODOLOGIA DE ANÁLISE

### Fase 1: Identificação Rápida
Ao receber qualquer informação, primeiro determine em 5 segundos:
- Qual é a informação principal? (preço, evento, análise, produção)
- Qual é o impacto potencial no mercado?
- Quem são os atores envolvidos? (países, empresas, portos)

### Fase 2: Classificação Inteligente
Categorize o conteúdo como:
- **[MERCADO COMPLETO]**: Contém preços + volumes + múltiplos indicadores
- **[EVENTO CRÍTICO]**: Notícia específica com impacto direto
- **[ANÁLISE ESTRATÉGICA]**: Perspectivas, tendências, previsões
- **[FLASH PREÇOS]**: Movimento de preços ou spreads intraday
- **[OPERACIONAL]**: Informações de produção, embarque, logística
- **[HÍBRIDO]**: Combina múltiplas categorias

### Fase 3: Extração Estruturada
Extraia com 100% de precisão:
- Preços spot e futuros (com contratos específicos)
- Percentuais de variação
- Volumes e tonelagens
- Spreads e diferenciais
- Datas e períodos de referência
- Geografia relevante e empresas mencionadas

### Fase 4: Síntese Inteligente
Crie um texto em português brasileiro que:
1. Comece com a informação mais impactante para trading
2. Forneça contexto necessário
3. Preserve relações de causa-efeito
4. Destaque implicações práticas

## REGRAS INEGOCIÁVEIS
1. **Precisão absoluta**: Jamais arredonde números
2. **Fidelidade total**: Não adicione interpretações pessoais
3. **Clareza técnica**: Mantenha terminologia (CFR, FOB, DCE, SGX)
4. **Distinção clara**: Separe fatos de especulações

## FORMATO DE OUTPUT
```
[CLASSIFICAÇÃO: tipo_identificado]
[ELEMENTOS PRESENTES: listar elementos encontrados]
[IMPACTO PRINCIPAL: resumir em uma linha]
[TÍTULO SUGERIDO: 5-8 palavras que capturem a essência]

[Seu texto analítico em português brasileiro]
```"""

CRITIQUE_SYSTEM = """# System Prompt para o Critique

Você é o editor-chefe de conteúdo de mercado da Minerals Trading, com 15 anos de experiência em commodities. Sua função é garantir qualidade máxima.

## FRAMEWORK DE REVISÃO

### Dimensão 1: Integridade da Informação (40%)
- Completude: Todas as informações capturadas?
- Precisão: Números e fatos 100% corretos?
- Contexto preservado?

### Dimensão 2: Relevância para Trading (30%)
- Informação mais importante no início?
- Impactos em preços claros?
- Riscos e oportunidades evidentes?

### Dimensão 3: Clareza e Organização (20%)
- Fluxo lógico correto?
- Termos técnicos consistentes?
- Sem ambiguidades?

### Dimensão 4: Formato (10%)
- Template ideal?
- Comprimento adequado?

## ESTRUTURA DO FEEDBACK

### VALIDAÇÃO INICIAL
✅ Classificação correta?
✅ Elementos identificados?
✅ Impacto bem definido?
✅ Título efetivo?

### ANÁLISE CRÍTICA
**CORREÇÕES OBRIGATÓRIAS**: 🔴 [Erros que DEVEM ser corrigidos]
**MELHORIAS IMPORTANTES**: 🟡 [Aspectos a melhorar]
**OTIMIZAÇÕES OPCIONAIS**: 🟢 [Refinamentos de valor]

### RECOMENDAÇÃO DE FORMATO
- Template ideal: [COMPLETO / FLASH / INSIGHT / OPERACIONAL]
- Comprimento ideal: [CONCISO / MÉDIO / DETALHADO]"""

CURATOR_SYSTEM = """# System Prompt para o Curator

Você é o especialista em comunicação mobile da Minerals Trading, responsável por criar mensagens perfeitas para WhatsApp.

## FILOSOFIA
- **Scannable**: Informação crítica visível imediatamente
- **Hierárquica**: Do mais importante para o complementar
- **Acionável**: Facilita tomada de decisão rápida

## FORMATAÇÃO MONOESPAÇADA
TODA mensagem DEVE começar e terminar com ``` (três crases).

## TEMPLATES

### RELATÓRIO DE MERCADO COMPLETO
```
📊 MINERALS TRADING // [Título Específico] // [Data]
─────────────────

### PREÇOS DE FECHAMENTO
[Contratos principais com variações]

### MOVIMENTO DO DIA
[Resumo em 2-3 linhas]

### DESTAQUES
- [Ponto mais importante]
- [Segundo ponto relevante]
```

### EVENTO CRÍTICO
```
📊 MINERALS TRADING // [Título do Evento]
─────────────────

⚠️ [EVENTO PRINCIPAL EM CAPS]

### IMPACTO IMEDIATO
[Descrição concisa]

### EXPECTATIVA DE MERCADO
[Reação esperada]
```

### ANÁLISE/INSIGHTS
```
📊 MINERALS TRADING // [Título da Análise]
─────────────────

### TENDÊNCIA PRINCIPAL
[Resumo em 2-3 linhas]

### DRIVERS DO MOVIMENTO
- [Fator principal]
- [Fator secundário]

### PERSPECTIVA
[Outlook de curto/médio prazo]
```

## REGRAS DE TÍTULO DINÂMICO
- Usar título validado pelo Critique
- Máximo 50 caracteres
- Comunicar a essência instantaneamente
- Exemplos: "Greve em Port Hedland Reduz Oferta", "DCE Sobe 3.5%"

## OTIMIZAÇÃO MOBILE
- Máximo 50-60 caracteres por linha
- Parágrafos de 2-4 linhas
- Info crítica nas primeiras 3 linhas
- Máximo 1500 caracteres

## REGRA ABSOLUTA DE OUTPUT
Produza APENAS a mensagem formatada. NADA antes ou depois.
Sem comentários, sem explicações, sem justificativas."""

ADJUSTER_SYSTEM = """Você é o Curator da Minerals Trading. Recebeu a mensagem final formatada para WhatsApp e o feedback do editor.

REGRAS:
1. Aplique APENAS os ajustes solicitados
2. Mantenha a formatação WhatsApp (começar e terminar com ```)
3. Mantenha o estilo e tom da mensagem original
4. Preserve todos os dados numéricos que não foram questionados
5. Produza APENAS a mensagem ajustada, sem comentários

OUTPUT: Apenas a mensagem ajustada, pronta para envio."""

# ============================================================
# TELEGRAM HELPERS
# ============================================================

def telegram_api(method, data):
    """Call Telegram Bot API and return parsed response."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    try:
        resp = requests.post(url, json=data, timeout=15)
        result = resp.json()
        if not result.get("ok"):
            logger.warning(f"Telegram {method} failed: {result.get('description', 'unknown')}")
        return result
    except Exception as e:
        logger.error(f"Telegram API error ({method}): {e}")
        return {"ok": False}

def answer_callback(callback_id, text):
    """Answer callback query (acknowledge button press)."""
    return telegram_api("answerCallbackQuery", {
        "callback_query_id": callback_id,
        "text": text
    })

def send_telegram_message(chat_id, text, reply_markup=None):
    """Send a message via Telegram."""
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    if reply_markup:
        data["reply_markup"] = reply_markup
    return telegram_api("sendMessage", data)

def edit_message(chat_id, message_id, text, reply_markup=None):
    """Edit an existing message."""
    data = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    if reply_markup:
        data["reply_markup"] = reply_markup
    return telegram_api("editMessageText", data)

def send_approval_message(chat_id, draft_id, preview_text):
    """Send preview with 3 approval buttons."""
    # Truncate preview for Telegram (max ~4096 chars)
    display_text = preview_text[:3500] if len(preview_text) > 3500 else preview_text
    
    buttons = {
        "inline_keyboard": [[
            {"text": "✅ Aprovar e Enviar", "callback_data": f"approve:{draft_id}"},
            {"text": "✏️ Ajustar", "callback_data": f"adjust:{draft_id}"},
            {"text": "❌ Rejeitar", "callback_data": f"reject:{draft_id}"}
        ]]
    }
    
    return send_telegram_message(chat_id, f"📋 *PREVIEW*\n\n{display_text}", buttons)

# ============================================================
# GOOGLE SHEETS (contacts)
# ============================================================

def get_contacts():
    """Fetch WhatsApp contacts from Google Sheets."""
    import gspread
    from google.oauth2.service_account import Credentials

    creds_json = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(creds_json, scopes=[
        "https://www.googleapis.com/auth/spreadsheets.readonly"
    ])
    gc = gspread.authorize(creds)
    sheet = gc.open_by_key(SHEET_ID).sheet1
    records = sheet.get_all_records()

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
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}]
    )
    return message.content[0].text

def run_3_agents(raw_text):
    """Run Writer → Critique → Curator chain. Returns final formatted message."""
    logger.info("Agent 1/3: Writer starting...")
    writer_output = call_claude(
        WRITER_SYSTEM,
        f"Processe e analise o seguinte conteúdo do mercado de minério de ferro.\n\nCONTEÚDO:\n---\n{raw_text}\n---\n\nProduza sua análise completa."
    )
    logger.info(f"Writer done ({len(writer_output)} chars)")

    logger.info("Agent 2/3: Critique starting...")
    critique_output = call_claude(
        CRITIQUE_SYSTEM,
        f"Revise o trabalho do Writer:\n\nTRABALHO DO WRITER:\n---\n{writer_output}\n---\n\nTEXTO ORIGINAL:\n---\n{raw_text}\n---\n\nExecute sua revisão crítica."
    )
    logger.info(f"Critique done ({len(critique_output)} chars)")

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
    """Process news text through 3 agents in background thread."""
    try:
        edit_message(chat_id, progress_msg_id, "⏳ Processando com IA (1/3 Writer)...")
        final_message = run_3_agents(raw_text)

        # Store draft
        import time
        draft_id = f"news_{int(time.time())}"
        DRAFTS[draft_id] = {
            "message": final_message,
            "status": "pending",
            "original_text": raw_text,
            "uazapi_token": None,
            "uazapi_url": None
        }

        # Remove progress message and send approval
        edit_message(chat_id, progress_msg_id, "✅ Processamento concluído!")
        send_approval_message(chat_id, draft_id, final_message)
        
        logger.info(f"News draft stored: {draft_id}")
    except Exception as e:
        logger.error(f"News processing error: {e}")
        edit_message(chat_id, progress_msg_id, f"❌ Erro no processamento:\n{str(e)[:500]}")

def process_adjustment_async(chat_id, draft_id, feedback):
    """Adjust draft with user feedback in background thread."""
    progress = send_telegram_message(chat_id, "⏳ Ajustando mensagem...")
    progress_msg_id = progress.get("result", {}).get("message_id") if progress.get("ok") else None
    
    try:
        draft = DRAFTS.get(draft_id)
        if not draft:
            send_telegram_message(chat_id, "❌ Draft não encontrado.")
            return

        adjusted = run_adjuster(draft["message"], feedback, draft["original_text"])
        
        # Update draft
        draft["message"] = adjusted
        draft["status"] = "pending"
        
        if progress_msg_id:
            edit_message(chat_id, progress_msg_id, "✅ Ajuste concluído!")
        
        send_approval_message(chat_id, draft_id, adjusted)
        logger.info(f"Draft {draft_id} adjusted")
    except Exception as e:
        logger.error(f"Adjustment error: {e}")
        if progress_msg_id:
            edit_message(chat_id, progress_msg_id, f"❌ Erro no ajuste:\n{str(e)[:500]}")

def process_approval_async(chat_id, draft_message, uazapi_token=None, uazapi_url=None):
    """Process WhatsApp sending in background thread with Telegram progress updates."""
    progress = send_telegram_message(chat_id, "⏳ Iniciando envio para WhatsApp...")
    progress_msg_id = progress.get("result", {}).get("message_id") if progress.get("ok") else None
    
    try:
        contacts = get_contacts()
        total = len(contacts)
        success_count = 0
        fail_count = 0
        
        if progress_msg_id:
            edit_message(chat_id, progress_msg_id, 
                f"⏳ Enviando para {total} contatos...\n0/{total} processados")
        
        for i, contact in enumerate(contacts):
            phone = contact.get("Evolution-api") or contact.get("Telefone")
            if not phone:
                continue
            phone = str(phone).replace("whatsapp:", "").strip()
            
            if send_whatsapp(phone, draft_message, token=uazapi_token, url=uazapi_url):
                success_count += 1
            else:
                fail_count += 1
            
            processed = success_count + fail_count
            if progress_msg_id and processed % 10 == 0:
                edit_message(chat_id, progress_msg_id,
                    f"⏳ Enviando...\n{processed}/{total} processados\n✅ {success_count} OK | ❌ {fail_count} falhas")
        
        result_text = f"📊 ENVIO CONCLUÍDO\n\n"
        result_text += f"✅ Enviados: {success_count}\n"
        result_text += f"❌ Falhas: {fail_count}\n"
        result_text += f"📋 Total: {total}\n"
        
        if fail_count == total:
            result_text += "\n⚠️ TODOS falharam! Verifique o token UAZAPI."
        
        if progress_msg_id:
            edit_message(chat_id, progress_msg_id, result_text)
        else:
            send_telegram_message(chat_id, result_text)
            
        logger.info(f"Approval complete: {success_count} sent, {fail_count} failed")
        
    except Exception as e:
        logger.error(f"Approval processing error: {e}")
        error_text = f"❌ ERRO NO ENVIO\n\n{str(e)}"
        if progress_msg_id:
            edit_message(chat_id, progress_msg_id, error_text)
        else:
            send_telegram_message(chat_id, error_text)

# ============================================================
# ROUTES
# ============================================================

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "drafts_count": len(DRAFTS),
        "uazapi_token_set": bool(UAZAPI_TOKEN),
        "uazapi_url": UAZAPI_URL,
        "anthropic_key_set": bool(ANTHROPIC_API_KEY)
    })

@app.route("/store-draft", methods=["POST"])
def store_draft():
    """Store a draft for later approval. Called by GitHub Actions."""
    data = request.json
    draft_id = data.get("draft_id")
    message = data.get("message")
    
    if not draft_id or not message:
        return jsonify({"error": "Missing draft_id or message"}), 400
    
    DRAFTS[draft_id] = {
        "message": message,
        "status": "pending",
        "original_text": "",
        "uazapi_token": (data.get("uazapi_token") or "").strip() or None,
        "uazapi_url": (data.get("uazapi_url") or "").strip() or None
    }
    
    if DRAFTS[draft_id]["uazapi_token"]:
        logger.info(f"Draft includes UAZAPI token: {DRAFTS[draft_id]['uazapi_token'][:8]}...")
    else:
        logger.info(f"Draft has no UAZAPI token, will use env var")
    
    logger.info(f"Draft stored: {draft_id} ({len(message)} chars)")
    return jsonify({"success": True, "draft_id": draft_id})

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
    
    # Ignore bot commands for now
    if text.startswith("/"):
        if text == "/start":
            send_telegram_message(chat_id, 
                "👋 *Minerals Trading Bot*\n\n"
                "Envie uma notícia de mercado e eu vou:\n"
                "1️⃣ Analisar com IA\n"
                "2️⃣ Formatar para WhatsApp\n"
                "3️⃣ Enviar para aprovação\n\n"
                "Basta colar o texto da notícia aqui!")
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

def handle_callback(callback_query):
    """Handle button press callbacks."""
    callback_id = callback_query["id"]
    callback_data = callback_query.get("data", "")
    chat_id = callback_query["message"]["chat"]["id"]
    
    logger.info(f"Callback: {callback_data} from chat {chat_id}")
    
    parts = callback_data.split(":")
    if len(parts) != 2:
        answer_callback(callback_id, "Erro: dados inválidos")
        return jsonify({"ok": True})
    
    action, draft_id = parts
    
    if action == "approve":
        draft = DRAFTS.get(draft_id)
        if not draft:
            logger.warning(f"Draft not found: {draft_id}")
            answer_callback(callback_id, "❌ Draft não encontrado")
            send_telegram_message(chat_id, "❌ DRAFT EXPIRADO\n\nRode o workflow novamente.")
            return jsonify({"ok": True})
        
        if draft["status"] != "pending":
            answer_callback(callback_id, "⚠️ Já processado")
            return jsonify({"ok": True})
        
        draft["status"] = "approved"
        answer_callback(callback_id, "✅ Aprovado! Enviando...")
        
        thread = threading.Thread(
            target=process_approval_async,
            args=(chat_id, draft["message"], draft.get("uazapi_token"), draft.get("uazapi_url"))
        )
        thread.daemon = True
        thread.start()
        return jsonify({"ok": True})
    
    elif action == "adjust":
        draft = DRAFTS.get(draft_id)
        if not draft:
            answer_callback(callback_id, "❌ Draft não encontrado")
            return jsonify({"ok": True})
        
        # Set adjustment state
        ADJUST_STATE[chat_id] = {
            "draft_id": draft_id,
            "awaiting_feedback": True
        }
        
        answer_callback(callback_id, "✏️ Modo ajuste")
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
        answer_callback(callback_id, "❌ Rejeitado")
        send_telegram_message(chat_id, "❌ REJEITADO\n\nEste relatório foi descartado.")
        if draft_id in DRAFTS:
            DRAFTS[draft_id]["status"] = "rejected"
        return jsonify({"ok": True})
    
    return jsonify({"ok": True})

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
