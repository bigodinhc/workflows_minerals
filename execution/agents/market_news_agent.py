
from execution.integrations.claude_client import ClaudeClient
from execution.core.logger import WorkflowLogger


class MarketNewsAgent:
    """
    Two-agent chain for broader market news:
    1. Curator (filter + analyze batch of articles)
    2. Localizer (PT-BR + WhatsApp formatting)
    """

    def __init__(self):
        self.claude = ClaudeClient()
        self.logger = WorkflowLogger("MarketNewsAgent")

    def process(self, raw_text, date_str):
        """
        Runs the 2-agent chain: Curator -> Localizer.
        """
        self.logger.info("Starting Curator Phase (1/2)...")
        curated = self._run_curator(raw_text)

        self.logger.info("Starting Localizer Phase (2/2)...")
        final_message = self._run_localizer(curated, date_str)

        return final_message

    def _run_curator(self, text):
        system_prompt = """You are a senior commodities market analyst specializing in iron ore and ferrous metals, with 15+ years covering the seaborne market for Brazilian trading firms.

## YOUR TASK

From a batch of Platts/S&P Global market articles, identify the 3-5 most relevant stories for Brazilian iron ore traders and produce a structured intermediate analysis.

## SELECTION CRITERIA (ranked by priority)

1. **Price movements**: Spot, futures (SGX/DCE), index changes (IODEX, TSI, Platts)
2. **Major producer actions**: Vale, BHP, Rio Tinto, FMG, CSN, Samarco — production, shipments, guidance changes
3. **Supply/demand shifts**: Port inventories, steel mill utilization, restocking/destocking cycles
4. **Trade activity**: Significant cargo deals, tenders, premium/discount shifts
5. **Policy/regulatory**: China stimulus, environmental curbs, export taxes, trade restrictions
6. **Logistics/disruptions**: Weather, port congestion, rail/shipping issues

## WHAT TO SKIP

- Administrative articles (personnel changes, conference announcements)
- Duplicate coverage of the same event (keep the most detailed version)
- Markets not relevant to iron ore/steel (oil, agriculture, etc.)
- Historical retrospectives without current trading implications

## OUTPUT FORMAT

For each selected article, produce:

```
=== STORY [N] (Relevance: HIGH/MEDIUM) ===
HEADLINE: [concise English headline]
KEY FACTS:
- [fact 1 with exact numbers]
- [fact 2]
- [fact 3]
TRADING IMPACT: [1-2 sentences on why this matters for Brazilian traders]
```

After all stories, add:

```
=== MARKET SNAPSHOT ===
OVERALL SENTIMENT: [Bullish/Bearish/Mixed] — [one-line reason]
TOP PRICE POINTS: [list key prices mentioned across all articles]
```

## RULES
1. NEVER fabricate information not present in the source articles
2. Preserve ALL numerical values exactly as written
3. Keep technical terms intact (CFR, FOB, dmt, Fe, IODEX, etc.)
4. Distinguish confirmed facts from market speculation
5. If fewer than 3 relevant articles exist, include only what qualifies"""

        user_prompt = f"""Analyze the following batch of Platts market articles. Select the 3-5 most relevant for Brazilian iron ore traders and produce your structured analysis.

ARTICLES BATCH:
---
{text}
---

Produce your curated analysis now."""

        return self.claude.generate_text(system_prompt, user_prompt)

    def _run_localizer(self, curated_text, date_str):
        system_prompt = """Você é um especialista em comunicação para o mercado de commodities brasileiro. Sua função é criar a MENSAGEM FINAL para envio via WhatsApp a traders de minério de ferro.

REGRAS CRÍTICAS:

1. IDIOMA: TUDO deve ser escrito em PORTUGUÊS BRASILEIRO.
   - Traduza todo o conteúdo para PT-BR
   - Apenas termos técnicos de mercado podem ficar em inglês (CFR, FOB, dmt, Fe, IODEX, etc.)
   - Títulos e seções SEMPRE em português
   - Exemplo: "Iron ore prices slipped" → "Os preços do minério de ferro recuaram"

2. MOEDA: NUNCA converta para BRL. SEMPRE mantenha preços em USD ($).
   - Correto: "Minério a $130,50/dmt"
   - Errado: "Minério a R$ 750,00"

3. CONTEÚDO - O QUE INCLUIR:
   - Resumo do mercado em texto corrido (narrativa fluida)
   - Preços-chave com valores exatos
   - Destaques de negociação
   - Perspectiva de curto prazo

4. CONTEÚDO - O QUE NÃO INCLUIR:
   - NÃO inclua "Classificação", "Humor do Mercado", "Eventos Críticos"
   - NÃO inclua seções de análise estruturada interna
   - NÃO inclua "Síntese Narrativa" como título de seção
   - NÃO inclua checklist, metodologia ou notas técnicas internas
   - Se algum dado estiver vazio ou genérico, OMITA completamente
   - Se não houver destaques, retorne apenas: "Sem destaques relevantes para hoje."

5. FORMATAÇÃO WHATSAPP:
   - Iniciar e terminar com ```
   - Primeira linha: 📊 MINERALS TRADING // [TÍTULO DINÂMICO EM PORTUGUÊS] // [DATA]
   - Use ### para separar seções (ex: ### RESUMO, ### PREÇOS-CHAVE, ### DESTAQUES)
   - Números: vírgula para decimais (100,20) mas SEMPRE EM USD
   - Máximo 1500 caracteres
   - Texto limpo, profissional, pronto para leitura rápida por trader"""

        user_prompt = f"""Crie a mensagem FINAL para WhatsApp baseada nesta análise curada de mercado.

IMPORTANTE:
- Gere APENAS a mensagem formatada para o usuário final
- TUDO EM PORTUGUÊS BRASILEIRO (exceto termos técnicos de mercado)
- Não inclua metadados internos

ANÁLISE CURADA:
{curated_text}

DATA: {date_str}

Gere a mensagem final formatada para WhatsApp, INTEIRAMENTE EM PORTUGUÊS."""

        return self.claude.generate_text(system_prompt, user_prompt)
