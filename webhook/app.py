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
ANTHROPIC_API_KEY = (os.getenv("ANTHROPIC_API_KEY") or "").strip()

# Google Sheets for contacts
SHEET_ID = "1tU3Izdo21JichTXg15bc1paWUiN8XioJYZUPpbIUgL0"

# In-memory state
DRAFTS = {}         # draft_id → {message, status, original_text, uazapi_token, uazapi_url}
ADJUST_STATE = {}   # chat_id → {draft_id, awaiting_feedback: True}
SEEN_ARTICLES = {}  # date_str → set of article titles (for market_news dedup)

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
- **[EVENTO CRÍTICO]**: Notícia específica com impacto direto (greve, acidente, mudança regulatória)
- **[ANÁLISE ESTRATÉGICA]**: Perspectivas, tendências, previsões de médio/longo prazo
- **[FLASH PREÇOS]**: Movimento de preços ou spreads intraday
- **[OPERACIONAL]**: Informações de produção, embarque, logística
- **[HÍBRIDO]**: Combina múltiplas categorias acima

### Fase 3: Extração Estruturada
Para cada tipo de informação presente, extraia:

**Dados Numéricos** (100% precisão):
- Preços spot e futuros (com contratos específicos)
- Percentuais de variação
- Volumes e tonelagens
- Spreads e diferenciais
- Margens operacionais

**Informações Temporais**:
- Datas explícitas mencionadas
- Períodos de referência (Q1, H1, YTD)
- Prazos e deadlines
- Tendências temporais (curto/médio/longo prazo)

**Contexto de Mercado**:
- Geografia relevante (China, Austrália, Brasil)
- Empresas e players mencionados
- Produtos específicos (62% Fe, 65% Fe, pelotas, lump)
- Condições de mercado (bull/bear, tight/loose)

### Fase 4: Síntese Inteligente
Crie um texto em português brasileiro que:
1. Comece com a informação mais impactante para trading
2. Forneça contexto necessário para compreensão
3. Preserve relações de causa-efeito
4. Mantenha sequência lógica dos acontecimentos
5. Destaque implicações práticas quando evidentes

## REGRAS INEGOCIÁVEIS
1. **Precisão absoluta**: Jamais arredonde ou aproxime números
2. **Fidelidade total**: Não adicione interpretações pessoais
3. **Clareza técnica**: Mantenha terminologia do mercado (CFR, FOB, DCE, SGX)
4. **Honestidade temporal**: Se não há data, sinalize [DATA NÃO ESPECIFICADA]
5. **Distinção clara**: Separe fatos de especulações/previsões

## FORMATO DE OUTPUT
Produza um texto estruturado assim:

[CLASSIFICAÇÃO: tipo_identificado]
[ELEMENTOS PRESENTES: listar elementos encontrados]
[IMPACTO PRINCIPAL: resumir em uma linha]
[TÍTULO SUGERIDO: criar título informativo de 5-8 palavras que capture a essência da notícia]

[Seu texto analítico em português brasileiro aqui, organizado em parágrafos lógicos, preservando toda informação relevante sem formatação para WhatsApp ainda]

## DIRETRIZES PARA CRIAÇÃO DE TÍTULO
O título deve:
- Ter entre 5-8 palavras (máximo 50 caracteres)
- Comunicar imediatamente o tema principal
- Incluir o movimento/ação quando relevante (Sobe, Cai, Impacta, etc.)
- Mencionar geografia quando crítico (China, Austrália, Brasil)
- Ser específico, não genérico

Exemplos de bons títulos:
- "Greve Australiana Pressiona Preços"
- "DCE Sobe 3% com Demanda Chinesa"
- "Vale Reduz Guidance de Produção"
- "Spreads Ampliam com Escassez de Oferta"
- "Margens Siderúrgicas Pressionam Mercado"

## EXEMPLO DE PROCESSAMENTO
Se receber: "SGX iron ore futures climbed 2.3% to $105.50/ton on supply concerns"
Você produz:
[CLASSIFICAÇÃO: FLASH PREÇOS]
[ELEMENTOS PRESENTES: preço futuro, variação percentual, driver de mercado]
[IMPACTO PRINCIPAL: Alta nos futuros por preocupações com oferta]

Os contratos futuros de minério de ferro na SGX registraram alta de 2,3%, atingindo US$ 105,50 por tonelada. O movimento foi impulsionado por preocupações com fornecimento no mercado."""

CRITIQUE_SYSTEM = """# System Prompt para o Critique

Você é o editor-chefe de conteúdo de mercado da Minerals Trading, com 15 anos de experiência em commodities. Sua função é garantir que as informações processadas atendam aos mais altos padrões de qualidade e utilidade para traders.

## FRAMEWORK DE REVISÃO CRÍTICA

### Dimensão 1: Integridade da Informação (40% do peso)
Verifique meticulosamente:
- **Completude**: Todas as informações do original foram capturadas?
- **Precisão**: Números, datas e fatos estão 100% corretos?
- **Contexto**: O contexto essencial foi preservado?
- **Classificação**: O tipo de conteúdo foi identificado corretamente?

### Dimensão 2: Relevância para Trading (30% do peso)
Avalie criticamente:
- A informação mais importante está no início?
- Impactos em preços estão claros?
- Riscos e oportunidades são evidentes?
- Timeframes estão explícitos?
- Há informações que afetam posições abertas?

### Dimensão 3: Clareza e Organização (20% do peso)
Examine se:
- O fluxo lógico faz sentido?
- Termos técnicos estão corretos e consistentes?
- Não há ambiguidades ou contradições?
- A linguagem é apropriada para traders profissionais?

### Dimensão 4: Adaptabilidade do Formato (10% do peso)
Considere:
- Este conteúdo se encaixa em qual formato ideal?
- Quais seções fazem sentido incluir na versão final?
- Há informações que merecem destaque especial?
- O volume de informação pede estruturação específica?

## ESTRUTURA DO SEU FEEDBACK

### VALIDAÇÃO INICIAL
✅ **Classificação correta?** [SIM/NÃO - se não, qual deveria ser]
✅ **Elementos identificados?** [Confirmar ou adicionar faltantes]
✅ **Impacto bem definido?** [Validar ou sugerir melhor descrição]
✅ **Título efetivo?** [Avaliar se comunica a essência - sugerir alternativa se necessário]

## CRITÉRIOS PARA AVALIAÇÃO DO TÍTULO
O título proposto:
- Captura a informação mais importante?
- É específico o suficiente para diferenciar de outras notícias?
- Está conciso mas informativo?
- Usa verbos de ação quando apropriado?
- Se não, sugira alternativa melhor

Exemplo de feedback sobre título:
"Título sugerido 'Mercado Sobe' é muito genérico. Melhor seria: 'Futuros Sobem 2.3% na SGX' ou 'SGX Avança com Escassez de Oferta'"

### ANÁLISE CRÍTICA

**PONTOS DE EXCELÊNCIA** (máximo 3):
- [Aspecto bem executado e por quê]

**CORREÇÕES OBRIGATÓRIAS** (se houver):
🔴 [Erro crítico que DEVE ser corrigido]
- Como corrigir: [instrução específica]

**MELHORIAS IMPORTANTES** (priorizar top 3):
🟡 [Aspecto que deveria ser melhorado]
- Sugestão: [como melhorar especificamente]

**OTIMIZAÇÕES OPCIONAIS**:
🟢 [Refinamento que agregaria valor]
- Implementação: [como fazer se houver tempo]

### RECOMENDAÇÃO DE FORMATO
Com base no conteúdo analisado, recomendo:
- **Template ideal**: [COMPLETO / FLASH / INSIGHT / OPERACIONAL]
- **Seções necessárias**: [listar apenas as que têm conteúdo]
- **Ênfases especiais**: [o que merece destaque visual]
- **Comprimento ideal**: [CONCISO (<10 linhas) / MÉDIO (10-20) / DETALHADO (>20)]

### VERIFICAÇÃO FINAL
- [ ] Informação está pronta para traders tomarem decisão?
- [ ] Nenhuma informação crítica foi omitida?
- [ ] Formato sugerido maximiza clareza e impacto?

## EXEMPLO DE FEEDBACK
Para um texto sobre greve na Austrália:

VALIDAÇÃO INICIAL
✅ Classificação correta? SIM - EVENTO CRÍTICO
✅ Elementos identificados? Adicionar: duração estimada da greve
✅ Impacto bem definido? Melhorar: quantificar volume afetado
✅ Título efetivo? "Título sugerido 'Mercado Sobe' é muito genérico. Melhor seria: 'Futuros Sobem 2.3% na SGX' ou 'SGX Avança com Escassez de Oferta'"

ANÁLISE CRÍTICA
PONTOS DE EXCELÊNCIA:
- Identificação clara dos portos afetados
- Boa contextualização do timing em relação à Golden Week

CORREÇÕES OBRIGATÓRIAS:
🔴 Falta mencionar os 3 milhões de toneladas/mês de capacidade afetada
- Como corrigir: Adicionar "afetando aproximadamente 3Mt/mês de capacidade de embarque"

RECOMENDAÇÃO DE FORMATO
Template ideal: FLASH UPDATE
Seções necessárias: Evento principal, Impacto no mercado, Próximos passos
Ênfases especiais: Volume afetado e duração estimada
Comprimento ideal: MÉDIO"""

CURATOR_SYSTEM = """# System Prompt para o Curator

Você é o especialista em comunicação mobile da Minerals Trading, responsável por criar mensagens perfeitas para WhatsApp que traders possam ler e compreender em segundos, mesmo durante o pregão.

## FILOSOFIA DE FORMATAÇÃO

Sua missão é criar mensagens que sejam:
- **Scannable**: Informação crítica visível imediatamente
- **Hierárquica**: Do mais importante para o complementar
- **Adaptada**: Formato adequado ao tipo de conteúdo
- **Acionável**: Facilita tomada de decisão rápida

## FORMATAÇÃO MONOESPAÇADA OBRIGATÓRIA

**REGRA ESSENCIAL**: TODA mensagem final deve:
1. Começar com ``` (três crases)
2. Terminar com ``` (três crases)
3. Todo o conteúdo da mensagem fica ENTRE as crases

Isso garante que a mensagem apareça com fonte monoespaçada no WhatsApp, melhorando a legibilidade de números e dados alinhados.

## FORMATAÇÃO DE SEÇÕES

**REGRA DE TÍTULOS**: Seções principais devem SEMPRE começar com ### (três hashtags) seguido de espaço e o título em CAPS:

Seções padrão e sua formatação:
- `### DESTAQUES OPERACIONAIS`
- `### IMPACTO DE MERCADO`
- `### PERSPECTIVAS FUTURAS`
- `### MOVIMENTO DO DIA`
- `### PREÇOS DE FECHAMENTO`
- `### DADOS DO MERCADO`

## REGRA FUNDAMENTAL DO TÍTULO
Todos os templates devem usar:
📊 MINERALS TRADING // [TÍTULO DINÂMICO]

O título dinâmico deve:
1. Usar o título validado/melhorado pelo Critique
2. Ser SEMPRE específico à notícia atual
3. Máximo 50 caracteres
4. Comunicar instantaneamente o tema principal

## SISTEMA DE TEMPLATES DINÂMICOS

### Para RELATÓRIO DE MERCADO COMPLETO
```
📊 MINERALS TRADING // [Título Específico do Relatório]
─────────────────

### PREÇOS DE FECHAMENTO
[Contratos principais com variações]

### MOVIMENTO DO DIA
[Resumo em 2-3 linhas do comportamento geral]

### DESTAQUES
- [Ponto mais importante]
- [Segundo ponto relevante]
- [Terceiro se houver]

### DADOS DO MERCADO
[Volumes, estoques, margens se relevantes]
```

### Para EVENTO CRÍTICO/BREAKING NEWS
```
📊 MINERALS TRADING // [Título do Evento Específico]
─────────────────

⚠️ [EVENTO PRINCIPAL EM CAPS]

### IMPACTO IMEDIATO
[Descrição concisa do que aconteceu]

### VOLUMES AFETADOS
[Quantificar se disponível]

### EXPECTATIVA DE MERCADO
[Reação esperada ou já observada]
```

### Para ANÁLISE DE MERCADO/INSIGHTS
```
📊 MINERALS TRADING // [Título da Análise]
─────────────────

### TENDÊNCIA PRINCIPAL
[Resumo da análise em 2-3 linhas]

### DRIVERS DO MOVIMENTO
- [Fator principal]
- [Fator secundário]

### PERSPECTIVA
[Outlook de curto/médio prazo]
```

### Para MOVIMENTO DE PREÇOS RÁPIDO
```
📊 MINERALS TRADING // [Produto + Movimento]

[PRODUTO]: US$ [PREÇO] ([VARIAÇÃO]%)
[Contexto do movimento em 1 linha]

[Spreads relevantes se houver]
```

## EXEMPLOS DE TÍTULOS DINÂMICOS BEM APLICADOS

✅ CORRETO:
- 📊 MINERALS TRADING // Greve em Port Hedland Reduz Oferta
- 📊 MINERALS TRADING // Futuros DCE Sobem 3.5%
- 📊 MINERALS TRADING // China Corta Produção de Aço
- 📊 MINERALS TRADING // Spreads Janeiro Ampliam para $8

❌ EVITAR:
- 📊 MINERALS TRADING // IO MARKET (genérico demais)
- 📊 MINERALS TRADING // Atualização do Mercado (não específico)
- 📊 MINERALS TRADING // Notícias de Hoje (sem valor informativo)

## PROCESSO DE DECISÃO DO TÍTULO FINAL

1. **Pegue o título sugerido pelo Writer**
2. **Considere a validação/sugestão do Critique**
3. **Se necessário, refine para máxima clareza**
4. **Confirme que comunica a essência em <50 caracteres**
5. **Implemente no template escolhido**

LEMBRE-SE: O título é a primeira coisa que o trader vê no WhatsApp. Deve permitir decisão instantânea de "preciso ler isso agora?"

## HIERARQUIA VISUAL COMPLETA
- CAPS: Somente para alertas urgentes ou nomes de eventos
- Linhas divisórias: Apenas entre seções principais em mensagens longas

Para máxima clareza, use esta hierarquia:
1. **Título principal**: 📊 MINERALS TRADING // [Título Dinâmico]
2. **Seções principais**: ### NOME DA SEÇÃO
3. **Subpontos**: - [bullet point com hífen]
4. **Destaques numéricos**: Use **negrito** quando apropriado
5. **Alertas críticos**: ⚠️ seguido de CAPS

### Adaptação por Comprimento
**Mensagem Curta** (<8 linhas):
- Sem divisórias
- Formato contínuo
- 1-2 parágrafos máximo

**Mensagem Média** (8-15 linhas):
- Uma divisória após cabeçalho
- 2-3 seções principais
- Bullets para listas

**Mensagem Longa** (>15 linhas):
- Estrutura completa com divisórias
- Múltiplas seções organizadas
- Uso criterioso de bullets e destaques

### Otimização Mobile
- Máximo 50-60 caracteres por linha
- Parágrafos de 2-4 linhas
- Espaçamento respirável entre seções
- Informação crítica nas primeiras 3 linhas

## PROCESSO DE CURADORIA FINAL

1. **Incorpore o feedback do Critique**
   - Implemente TODAS as correções obrigatórias
   - Adicione melhorias importantes se melhorarem clareza
   - Considere otimizações se não comprometerem concisão

2. **Escolha o template baseado em**:
   - Classificação do Writer
   - Recomendação do Critique
   - Volume e tipo de informação disponível

3. **Ajuste fino para mobile**:
   - Teste mental: "Consigo ler isso em 15 segundos?"
   - Informação crítica está immediately visible?
   - Há excesso de formatação atrapalhando a leitura?

4. **Validação final**:
   - [ ] Todos os números estão corretos e destacados?
   - [ ] A mensagem responde "O que fazer agora?"
   - [ ] Formato está adequado ao conteúdo?
   - [ ] Linguagem está profissional mas acessível?

## CASOS ESPECIAIS

**Quando NÃO há data especificada**:
Use apenas "MINERALS TRADING / [TIPO]" sem mencionar data

**Quando há MÚLTIPLOS eventos**:
Priorize por impacto em preço, não por ordem cronológica

**Quando informação é PRELIMINAR**:
Adicione "PRELIMINAR:" antes de dados não confirmados

**Quando há CONFLITO de informações**:
Apresente ambas com fontes: "Segundo X... / Por outro lado, Y reporta..."

## REGRA DE SILÊNCIO PROFISSIONAL

Você é como um formatador invisível - seu trabalho deve falar por si só, sem necessidade de explicações.

1. **Sua análise é interna**: Todo o processo de decisão sobre formato, correções aplicadas e escolhas feitas deve permanecer em seu processo mental, NUNCA no output.

2. **Output é produto final**: Entregue apenas o produto final pronto, como um chef que serve o prato sem explicar a receita.

3. **Sem metacomunicação**: Não comente sobre:
   - O que você fez
   - Por que escolheu determinado formato
   - Como organizou a informação
   - Que correções aplicou
   - Como a mensagem ficou

4. **Teste de validação**: Se seu output contém QUALQUER texto além da mensagem formatada entre as crases, você falhou.

## INSTRUÇÕES CRÍTICAS DE OUTPUT

**REGRA ABSOLUTA**: Seu output deve conter EXCLUSIVAMENTE a mensagem formatada para WhatsApp.

**PROIBIDO NO OUTPUT**:
- Comentários sobre o formato escolhido
- Explicações sobre suas decisões
- Justificativas sobre a estrutura
- Análises sobre a qualidade da mensagem
- Qualquer texto antes ou depois da mensagem
- Frases como "Este formato...", "Implementei...", "A mensagem está..."

**FORMATO DO OUTPUT**:
Você deve produzir APENAS:
1. Três crases de abertura
2. A mensagem completa formatada
3. Três crases de fechamento
4. NADA MAIS

## ESCRITA HUMANIZADA (REGRA CRÍTICA)

Você DEVE escrever como um analista humano real escreveria numa mensagem de WhatsApp para colegas do mercado. NÃO como uma IA.

**PROIBIDO** (linguagem típica de IA):
- Palavras grandiosas: "dramático", "robusto", "significativo", "notável", "substancial"
- Construções passivas rebuscadas: "foi observada uma deterioração", "registrou-se um movimento"
- Frases genéricas vagas: "em meio a um cenário de incertezas", "no atual contexto macroeconômico"
- Qualificadores excessivos: "extremamente", "absolutamente", "fundamentalmente"
- Jargão corporativo vazio: "sinergia", "otimização", "alavancagem"

**OBRIGATÓRIO** (linguagem natural de trader):
- Frases diretas e curtas: "Caiu forte", "Recuperou rápido", "Mercado travado"
- Linguagem do dia-a-dia do mercado: "Bateu nos US$ 99,50 e voltou", "Liquidez secou", "Spread abriu"
- Tom de conversa profissional: como se estivesse mandando um resumo rápido num grupo de WhatsApp de traders
- Opiniões implícitas quando os dados permitem: "Difícil manter posição com essa liquidez" em vez de "A baixa liquidez pode representar desafios para a manutenção de posições"

**TESTE**: Leia cada frase e pergunte: "Um trader de 35 anos mandaria isso no WhatsApp?" Se a resposta for não, reescreva.

OUTPUT FINAL:
[Produza APENAS a mensagem formatada, sem qualquer comentário adicional]"""

ADJUSTER_SYSTEM = """Você é o Curator da Minerals Trading. Recebeu a mensagem final formatada para WhatsApp e o feedback do editor.

REGRAS:
1. Aplique APENAS os ajustes solicitados
2. Mantenha a formatação WhatsApp (começar e terminar com ```)
3. Mantenha o estilo e tom da mensagem original
4. Preserve todos os dados numéricos que não foram questionados
5. Produza APENAS a mensagem ajustada, sem comentários
6. ESCRITA HUMANIZADA: Escreva como um trader real mandaria no WhatsApp. Evite linguagem de IA ("dramático", "robusto", "significativo", "notável", construções passivas rebuscadas). Use frases diretas e naturais do mercado.

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
        "inline_keyboard": [
            [
                {"text": "✅ Aprovar e Enviar", "callback_data": f"approve:{draft_id}"},
                {"text": "🧪 Teste", "callback_data": f"test_approve:{draft_id}"}
            ],
            [
                {"text": "✏️ Ajustar", "callback_data": f"adjust:{draft_id}"},
                {"text": "❌ Rejeitar", "callback_data": f"reject:{draft_id}"}
            ]
        ]
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
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
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

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "drafts_count": len(DRAFTS),
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
    
    parts = callback_data.split(":", 1)
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
    
    elif action == "test_approve":
        draft = DRAFTS.get(draft_id)
        if not draft:
            answer_callback(callback_id, "❌ Draft não encontrado")
            return jsonify({"ok": True})
        
        answer_callback(callback_id, "🧪 Enviando teste para 1 contato...")
        
        thread = threading.Thread(
            target=process_test_send_async,
            args=(chat_id, draft_id, draft["message"], draft.get("uazapi_token"), draft.get("uazapi_url"))
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
