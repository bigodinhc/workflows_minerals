
import json
from execution.integrations.claude_client import ClaudeClient
from execution.core.logger import WorkflowLogger

class RationaleAgent:
    """
    Simulates the n8n Agent Chain:
    1. Market Analyst (Extraction)
    2. Synthesis Specialist (Briefing)
    3. Brazil Localizer (Translation & Adaptation)
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
        system_prompt = """
        You are a senior commodities market analyst specializing in iron ore.
        
        YOUR ANALYTICAL FRAMEWORK:
        
        Phase 1: Classification
        - MARKET REPORT (Trading activity, sentiment)
        - PRICING RATIONALE (Technical price assessments)
        - MIXED
        
        Phase 2: Intelligence Extraction
        - Price Movements (Spot, Indices, Regional)
        - Trading Activity (Major trades, volumes)
        - Market Drivers (Policies, supply/demand)
        - Company Actions
        
        Phase 3: Output Structure
        Create a comprehensive analysis. Preserve all numbers exactly.
        """
        
        user_prompt = f"""
        Analyze the following iron ore market data:
        
        ---
        {text}
        ---
        
        Structure your output with:
        [CLASSIFICATION]
        [KEY METRIC]
        [MARKET MOOD]
        [CRITICAL EVENTS]
        
        PRICE SUMMARY
        MARKET DYNAMICS
        TRADING HIGHLIGHTS
        FORWARD INDICATORS
        """
        
        return self.claude.generate_text(system_prompt, user_prompt)
        
    def _run_synthesis(self, analysis, original_text):
        system_prompt = """
        You are a financial communication specialist. Transform complex analysis into a 30-second briefing.
        
        METHODOLOGY:
        1. Lead with impact (most important info first)
        2. Layer information (Critical -> Important -> Context)
        3. Maintain flow
        4. Preserve precision (Never round numbers)
        
        OUTPUT FORMAT:
        A single, flowing text with natural paragraph breaks. No bullet points.
        """
        
        user_prompt = f"""
        Synthesize this analysis into a cohesive narrative:
        
        ANALYSIS:
        {analysis}
        
        ORIGINAL DATA (For Reference):
        {original_text}
        """
        
        return self.claude.generate_text(system_prompt, user_prompt)
        
    def _run_localizer(self, synthesis, analysis, original, date_str):
        system_prompt = """
        Você é um especialista em mercado financeiro brasileiro. Adapte a análise para traders brasileiros.
        
        FILOSOFIA:
        - Adaptar, não traduzir cegamente.
        - Contexto Brasil (Vale, CSN, impactos locais).
        - Terminologia correta (Spot, CFR, FOB).
        
        REGRAS DE FORMATAÇÃO WHATSAPP:
        1. Iniciar e terminar com ```
        2. Título: 📊 MINERALS TRADING // [TÍTULO DINÂMICO] // [DATA]
        3. Use ### para seções
        4. Números: vírgula para decimais (105,15)
        5. Máximo 1500 chars
        """
        
        user_prompt = f"""
        Adapte para o Brasil:
        
        SÍNTESE NARRATIVA:
        {synthesis}
        
        ANÁLISE ESTRUTURADA:
        {analysis}
        
        DATA: {date_str}
        
        Gere a mensagem final formatada.
        """
        
        return self.claude.generate_text(system_prompt, user_prompt)
