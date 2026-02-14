
from execution.integrations.claude_client import ClaudeClient
from execution.core.logger import WorkflowLogger


class MarketNewsAgent:
    """
    Single-agent processor for market news:
    Localizer takes all articles and produces PT-BR WhatsApp message.
    No filtering — user approves/rejects via Telegram.
    """

    def __init__(self):
        self.claude = ClaudeClient()
        self.logger = WorkflowLogger("MarketNewsAgent")

    def process(self, raw_text, date_str):
        """
        Runs the Localizer on all articles.
        """
        self.logger.info("Starting Localizer...")
        final_message = self._run_localizer(raw_text, date_str)
        return final_message

    def _run_localizer(self, raw_text, date_str):
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
   - Resumo de TODOS os artigos recebidos (não filtre nenhum)
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

        user_prompt = f"""Crie a mensagem FINAL para WhatsApp baseada nos artigos de mercado abaixo.

IMPORTANTE:
- Gere APENAS a mensagem formatada para o usuário final
- TUDO EM PORTUGUÊS BRASILEIRO (exceto termos técnicos de mercado)
- Inclua informações de TODOS os artigos, sem filtrar nenhum
- Não inclua metadados internos

ARTIGOS DE MERCADO:
{raw_text}

DATA: {date_str}

Gere a mensagem final formatada para WhatsApp, INTEIRAMENTE EM PORTUGUÊS."""

        return self.claude.generate_text(system_prompt, user_prompt)
