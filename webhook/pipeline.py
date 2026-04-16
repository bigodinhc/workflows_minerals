"""
Claude AI 3-agent pipeline for iron ore market news processing.

Agents:
  Writer   — drafts the WhatsApp message from raw content
  Reviewer — critiques the draft for accuracy and tone
  Finalizer (Curator) — produces the final formatted message
  Adjuster — applies user feedback to an existing draft
"""

import os
import logging
import anthropic

from execution.core.prompts import WRITER_SYSTEM, CRITIQUE_SYSTEM, CURATOR_SYSTEM, ADJUSTER_SYSTEM

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = (os.getenv("ANTHROPIC_API_KEY") or "").strip()


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
