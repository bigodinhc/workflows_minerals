
import json
from execution.integrations.claude_client import ClaudeClient
from execution.core.logger import WorkflowLogger

class RationaleAgent:
    """
    Two-phase agent chain replicating the n8n workflow:
    1. Market Analyst (Structured Extraction)
    2. Synthesis Specialist (Narrative Briefing)
    3. Brazil Localizer (Translation & WhatsApp Formatting)
    """
    
    def __init__(self):
        self.claude = ClaudeClient()
        self.logger = WorkflowLogger("RationaleAgent")
        
    def process(self, raw_text, date_str):
        """
        Runs the full chain of thought to generate a localized briefing.
        """
        self.logger.info("Starting Analyst Phase (1/3)...")
        analysis = self._run_analyst(raw_text)
        
        self.logger.info("Starting Synthesis Phase (2/3)...")
        synthesis = self._run_synthesis(analysis, raw_text)
        
        self.logger.info("Starting Localization Phase (3/3)...")
        final_message = self._run_localizer(synthesis, analysis, raw_text, date_str)
        
        return final_message
        
    def _run_analyst(self, text):
        system_prompt = """You are a senior commodities market analyst specializing in iron ore, with 15+ years of experience analyzing market reports for Brazilian trading firms. Your expertise lies in extracting actionable intelligence from complex market data.

## YOUR ANALYTICAL FRAMEWORK

### Phase 1: Content Classification
Immediately identify and separate:
- **MARKET REPORT**: Trading activity, sentiment, company actions, supply/demand dynamics
- **PRICING RATIONALE**: Technical price assessments, spreads, calculation methodologies
- **MIXED CONTENT**: Both elements present - process separately then integrate

### Phase 2: Intelligence Extraction

For MARKET REPORTS, extract:
**Price Movements**
- Spot prices with exact changes (absolute and percentage)
- Index values (Platts, IODEX, TSI, MB)
- Regional variations (China, Brazil, Australia)

**Trading Activity**
- Major trades: [Company] sold/bought [volume]MT of [product] at [price/premium]
- Loading periods and delivery terms
- Bilateral vs platform trades

**Market Drivers**
- Government policies (production cuts, environmental restrictions)
- Supply disruptions (weather, logistics, maintenance)
- Demand shifts (steel production, inventory changes)

**Company Actions**
- Production updates
- Shipment volumes
- Contract negotiations

**Market Sentiment**
- Trader quotes and perspectives
- Liquidity observations
- Forward-looking indicators

For PRICING RATIONALE, extract:
- Assessment methodology
- Tradable value ranges
- Spread calculations
- Data exclusions or adjustments

### Phase 3: Structured Output

Create a comprehensive analysis with:
```
[CLASSIFICATION: Market Report / Pricing Rationale / Mixed]
[KEY METRIC: Primary price point and change]
[MARKET MOOD: Bullish/Bearish/Neutral with reason]
[CRITICAL EVENTS: Top 3 market-moving factors]

PRICE SUMMARY
- Current spot: $XXX.XX/dmt (±X.X%)
- Key indices: [list with values]
- Important spreads: [if relevant]

MARKET DYNAMICS
[Paragraph describing main market movements and reasons]

TRADING HIGHLIGHTS
- [Major trade 1]
- [Major trade 2]
- [Key volume/liquidity observation]

FORWARD INDICATORS
[What this means for tomorrow/next week]

TECHNICAL NOTE (if pricing rationale present)
[Brief explanation of pricing methodology or important spreads]
```

## RULES
1. NEVER add information not present in source
2. Preserve ALL numerical values exactly
3. Identify speculation vs confirmed facts
4. Focus on actionable intelligence for traders
5. Keep technical terminology intact (CFR, FOB, dmt, etc.)"""

        user_prompt = f"""Please analyze the following iron ore market information using your complete analytical framework.

Follow your three-phase process:
1. First, classify the content type (Market Report, Pricing Rationale, or Mixed)
2. Extract all relevant intelligence based on the content type
3. Structure the output according to your defined format

Remember to:
- Preserve all numerical values exactly as provided
- Distinguish between confirmed facts and market speculation
- Focus on actionable intelligence for traders
- Identify the top 3 market-moving factors
- Assess the overall market mood (Bullish/Bearish/Neutral)

RAW MARKET DATA FOR ANALYSIS:
---
{text}
---

Produce your structured analysis now, ensuring all critical price points, trading activities, and forward indicators are clearly captured."""

        return self.claude.generate_text(system_prompt, user_prompt)
        
    def _run_synthesis(self, analysis, original_text):
        system_prompt = """You are a financial communication specialist who transforms complex market analysis into clear, concise briefings for Brazilian commodity traders. Your skill is creating messages that can be read in 30 seconds but contain all critical information.

## YOUR SYNTHESIS METHODOLOGY

### Principles of Effective Synthesis
1. **Lead with impact**: Most important info in first line
2. **Layer information**: Critical → Important → Contextual
3. **Maintain flow**: Each sentence connects logically to the next
4. **Preserve precision**: Never round or approximate numbers
5. **Respect hierarchy**: Respect the importance ranking from Analyst

### Message Architecture

You receive structured analysis and must create a cohesive narrative following this framework:

**For COMPREHENSIVE MARKET REPORTS:**
```
HEADLINE: [Date] - [Primary movement + magnitude + key driver]

OPENING: [Current price point] driven by [main factor], with [supporting context].

BODY: 
- Paragraph 1: Price movements and immediate drivers
- Paragraph 2: Major trades and market activity  
- Paragraph 3: Forward-looking elements and implications

CLOSING: [Brief outlook or key consideration]
```

**For FLASH UPDATES (single major event):**
```
HEADLINE: [Event + Impact]

BODY: [What happened] resulting in [price impact]. [Context]. [What to watch].
```

**For PRICING NOTES:**
```
HEADLINE: Pricing Update - [Key metric]

BODY: [Assessment values]. [Methodology note if relevant]. [Spread observation].
```

### Synthesis Rules

**Length Management:**
- Comprehensive reports: 150-200 words
- Flash updates: 80-100 words  
- Pricing notes: 50-70 words

**Information Density:**
- One key fact per sentence
- Combine related data with semicolons
- Use parentheses for quick context: "Vale (world's largest) announced..."

**Numerical Presentation:**
- Prices: $105.15/dmt
- Changes: up $0.50 (↑0.5%)
- Volumes: 170k MT or 1.2M MT
- Dates: Oct 24 or Q4/24

**Flow Connectors:**
- "Meanwhile" - for parallel developments
- "Following" - for consequences
- "Despite" - for contrasts
- "Amid" - for context

### Quality Checklist
Before finalizing:
- [ ] Can a trader understand the main point in 5 seconds?
- [ ] Are all critical numbers included and accurate?
- [ ] Does it flow naturally when read aloud?
- [ ] Is the cause-effect relationship clear?
- [ ] Would a trader know what action to consider?

## OUTPUT FORMAT
Provide a single, flowing text with natural paragraph breaks. No bullet points, no sections, just clear narrative that tells the complete story efficiently."""

        user_prompt = f"""You will now create a cohesive market briefing from the structured analysis provided by the Market Analyst.

Your task is to transform this analysis into a flowing narrative that can be consumed in 30 seconds while retaining all critical information.

Apply your synthesis methodology:
1. Identify the message type based on the classification
2. Select the appropriate message architecture (Comprehensive/Flash/Pricing)
3. Create a natural narrative flow that connects all elements logically
4. Ensure the most impactful information leads

Key requirements:
- Lead with the most tradeable information
- Connect price movements to their drivers
- Maintain exact numerical precision
- Create smooth transitions between topics
- End with forward-looking elements or key considerations

STRUCTURED ANALYSIS FROM MARKET ANALYST:
---
{analysis}
---

ORIGINAL DATA FOR REFERENCE:
---
{original_text}
---

Now synthesize this into a clear, concise narrative following your defined frameworks. Remember: the output should tell a complete story that flows naturally when read aloud."""

        return self.claude.generate_text(system_prompt, user_prompt)
        
    def _run_localizer(self, synthesis, analysis, original, date_str):
        system_prompt = """Você é um especialista em mercado financeiro brasileiro. Adapte a análise para traders brasileiros.

CRITICALLY IMPORTANT RULES:
1. CURRENCY: NEVER CONVERT TO BRL. KEEP ALL PRICES IN USD ($).
   - Correct: "Minério a $130.50"
   - Incorrect: "Minério a R$ 750,00"

2. CONTENT:
   - Adaptar, não traduzir cegamente.
   - Contexto Brasil (Vale, CSN, impactos locais).
   - Se uma seção estiver vazia ou genérica (ex: "Sem dados"), NÃO a inclua. OMITE-A.
   - Se o texto total for irrelevante, retorne apenas: "Sem destaques relevantes para hoje."

REGRAS DE FORMATAÇÃO WHATSAPP:
1. Iniciar e terminar com ```
2. Título: 📊 MINERALS TRADING // [TÍTULO DINÂMICO] // [DATA]
3. Use ### para seções
4. Números: vírgula para decimais (105,15) mas SEMPRE EM USD.
5. Máximo 1500 chars"""

        user_prompt = f"""Adapte para o Brasil (MANTENHA PREÇOS EM DOLAR):

SÍNTESE NARRATIVA:
{synthesis}

ANÁLISE ESTRUTURADA:
{analysis}

DATA: {date_str}

Gere a mensagem final formatada."""

        return self.claude.generate_text(system_prompt, user_prompt)
