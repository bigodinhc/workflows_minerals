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
from execution.integrations.sheets_client import SheetsClient

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

# In-memory state (ADJUST_STATE + SEEN_ARTICLES are ephemeral; DRAFTS now in Redis)
ADJUST_STATE = {}   # chat_id → {draft_id, awaiting_feedback: True}
SEEN_ARTICLES = {}  # date_str → set of article titles (for market_news dedup)


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

ALL_WORKFLOWS = [
    "morning_check",
    "daily_report",
    "baltic_ingestion",
    "market_news",
    "rationale_news",
]


def _format_status_lines(states: dict, next_runs: dict) -> list:
    """Build per-workflow lines for the /status response."""
    max_name = max(len(w) for w in states.keys()) if states else 0
    lines = []
    for workflow, st in states.items():
        # Escape underscores so Telegram Markdown doesn't interpret them as italic markers
        label = (workflow.replace("_", r"\_") + ":").ljust(max_name + 4)
        if st is not None and st.get("streak", 0) >= 3:
            lines.append(f"{label} 🚨 {st['streak']} falhas seguidas")
            continue
        if st is None:
            nxt = next_runs.get(workflow)
            when = nxt.strftime("%H:%M") if nxt else "?"
            lines.append(f"{label} ⏳ proximo {when} BRT")
            continue
        status = st.get("status")
        time_iso = st.get("time_iso", "")
        try:
            hhmm = time_iso[11:16]
        except Exception:
            hhmm = "??:??"
        if status == "success":
            summary = st.get("summary", {})
            ok = summary.get("success", 0)
            total = summary.get("total", 0)
            dur_ms = st.get("duration_ms", 0)
            dur = f"{dur_ms // 60000}m" if dur_ms >= 60000 else f"{dur_ms // 1000}s"
            lines.append(f"{label} ✅ {hhmm} BRT ({ok}/{total}, {dur})")
        elif status == "failure":
            summary = st.get("summary", {})
            total = summary.get("total", 0)
            lines.append(f"{label} ❌ {hhmm} BRT (0/{total} enviadas)")
        elif status == "crash":
            reason = (st.get("reason") or "")[:40]
            lines.append(f"{label} 💥 {hhmm} BRT (crash: {reason})")
        elif status == "empty":
            reason = st.get("reason", "")
            lines.append(f"{label} ℹ️ {hhmm} BRT ({reason})")
        else:
            lines.append(f"{label} ? estado desconhecido")
    return lines


def _build_status_message() -> str:
    """Fetch state + cron + format full /status body."""
    from execution.core import state_store, cron_parser
    from datetime import datetime, timezone, timedelta
    brt = timezone(timedelta(hours=-3))
    states = state_store.get_all_status(ALL_WORKFLOWS)
    if all(v is None for v in states.values()):
        # Probe if Redis itself is dead (not just "never recorded")
        if state_store._get_client() is None:
            return "⚠️ Store de estado indisponivel. Abra o dashboard pra ver historico."
    next_runs = {wf: cron_parser.parse_next_run(wf) for wf in ALL_WORKFLOWS}
    header = datetime.now(brt).strftime("📊 STATUS (%d/%m %H:%M BRT)")
    lines = _format_status_lines(states, next_runs)
    dashboard_url = os.getenv("DASHBOARD_BASE_URL", "https://workflows-minerals.vercel.app")
    return header + "\n\n" + "\n".join(lines) + f"\n\n[Dashboard]({dashboard_url}/)"


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


def finalize_card(chat_id, callback_query, status_text):
    """Final feedback for curation buttons: edit the original card; on failure send a new plain-text message.

    Removes the inline keyboard so the user can't double-click, and guarantees
    a visual confirmation even if the Markdown edit fails (old message, parse
    errors, etc.).
    """
    message_id = callback_query.get("message", {}).get("message_id")
    if not message_id:
        logger.warning("finalize_card: missing message_id in callback_query")
        send_telegram_message(chat_id, status_text)
        return

    edit_result = edit_message(chat_id, message_id, status_text, reply_markup=None)
    if edit_result.get("ok"):
        return

    # Edit failed (markdown parse error, msg too old, etc.) — fallback to a new plain message
    logger.warning(
        f"finalize_card: edit_message failed for msg_id={message_id}: "
        f"{edit_result.get('description', 'unknown')} — sending fallback"
    )
    # Strip markdown for safety in fallback
    plain = status_text.replace("*", "").replace("`", "").replace("_", "")
    send_telegram_message(chat_id, plain)

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
        drafts_set(draft_id, {
            "message": final_message,
            "status": "pending",
            "original_text": raw_text,
            "uazapi_token": None,
            "uazapi_url": None
        })

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
                body = _build_status_message()
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
        f"🤖 Reprocessando item `{item_id}` nos 3 agents...",
    )
    progress_msg_id = progress.get("result", {}).get("message_id") if progress else None
    threading.Thread(
        target=_run_pipeline_and_archive,
        args=(chat_id, raw_text, progress_msg_id, item_id),
        daemon=True,
    ).start()


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
            send_telegram_message(chat_id, "❌ DRAFT EXPIRADO\n\nRode o workflow novamente.")
            return jsonify({"ok": True})

        if draft["status"] != "pending":
            answer_callback(callback_id, "⚠️ Já processado")
            return jsonify({"ok": True})

        drafts_update(draft_id, status="approved")
        answer_callback(callback_id, "✅ Aprovado! Enviando...")

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
        draft = drafts_get(draft_id)
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
        if drafts_contains(draft_id):
            drafts_update(draft_id, status="rejected")
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
            f"✅ *Arquivado* em {datetime.now(timezone.utc).strftime('%H:%M')} UTC\n🆔 `{item_id}`",
        )
        return jsonify({"ok": True})

    elif action == "curate_reject":
        if not contact_admin.is_authorized(chat_id):
            answer_callback(callback_id, "Não autorizado")
            return jsonify({"ok": True})
        item_id = parts[1] if len(parts) > 1 else ""
        from execution.curation import redis_client
        try:
            redis_client.discard(item_id)
        except Exception as exc:
            logger.error(f"curate_reject redis error: {exc}")
            answer_callback(callback_id, "⚠️ Redis indisponível")
            return jsonify({"ok": True})
        answer_callback(callback_id, "❌ Recusado")
        finalize_card(
            chat_id,
            callback_query,
            f"❌ *Recusado* em {datetime.now(timezone.utc).strftime('%H:%M')} UTC\n🆔 `{item_id}`",
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
        raw_text = (
            f"Title: {item.get('title','')}\n"
            f"Date: {item.get('publishDate','')}\n"
            f"Source: {item.get('source','')}\n\n"
            f"{item.get('fullText','')}"
        )
        answer_callback(callback_id, "🤖 Processando nos 3 agents...")
        progress = send_telegram_message(chat_id, f"🤖 Processando item `{item_id}` nos 3 agents...")
        progress_msg_id = progress.get("result", {}).get("message_id") if progress else None
        # Finalize the original card BEFORE starting the thread so user sees confirmation immediately
        finalize_card(
            chat_id,
            callback_query,
            f"🤖 *Enviado aos 3 agents* em {datetime.now(timezone.utc).strftime('%H:%M')} UTC\n🆔 `{item_id}`",
        )
        threading.Thread(
            target=_run_pipeline_and_archive,
            args=(chat_id, raw_text, progress_msg_id, item_id),
            daemon=True,
        ).start()
        return jsonify({"ok": True})

    return jsonify({"ok": True})

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
