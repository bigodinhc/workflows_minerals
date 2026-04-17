"""Claude AI 3-agent pipeline for iron ore market news processing.

Agents:
  Writer   — drafts the WhatsApp message from raw content
  Reviewer — critiques the draft for accuracy and tone
  Finalizer (Curator) — produces the final formatted message
  Adjuster — applies user feedback to an existing draft

All functions are async — they use anthropic.AsyncAnthropic.
"""

import asyncio
import logging

import anthropic

from bot.config import ANTHROPIC_API_KEY
from execution.core.prompts import WRITER_SYSTEM, CRITIQUE_SYSTEM, CURATOR_SYSTEM, ADJUSTER_SYSTEM

logger = logging.getLogger(__name__)


async def call_claude(system_prompt: str, user_prompt: str) -> str:
    """Call Claude API (async) and return text response."""
    logger.info(f"call_claude: system={len(system_prompt)} chars, user={len(user_prompt)} chars")
    try:
        client = anthropic.AsyncAnthropic(
            api_key=ANTHROPIC_API_KEY,
            timeout=120.0,
        )
        message = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
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


async def run_3_agents(raw_text: str, on_phase_start=None) -> str:
    """Run Writer -> Critique -> Curator chain. Returns final formatted message.

    on_phase_start: optional callable(phase_name) invoked before each phase.
    If it is a coroutine function, it will be awaited.
    """
    async def _notify(phase_name):
        if on_phase_start is None:
            return
        result = on_phase_start(phase_name)
        if asyncio.iscoroutine(result):
            await result

    await _notify("Writer")
    logger.info("Agent 1/3: Writer starting...")
    writer_output = await call_claude(
        WRITER_SYSTEM,
        f"Processe e analise o seguinte conteúdo do mercado de minério de ferro.\n\nCONTEÚDO:\n---\n{raw_text}\n---\n\nProduza sua análise completa.",
    )
    logger.info(f"Writer done ({len(writer_output)} chars)")

    await _notify("Reviewer")
    logger.info("Agent 2/3: Critique starting...")
    critique_output = await call_claude(
        CRITIQUE_SYSTEM,
        f"Revise o trabalho do Writer:\n\nTRABALHO DO WRITER:\n---\n{writer_output}\n---\n\nTEXTO ORIGINAL:\n---\n{raw_text}\n---\n\nExecute sua revisão crítica.",
    )
    logger.info(f"Critique done ({len(critique_output)} chars)")

    await _notify("Finalizer")
    logger.info("Agent 3/3: Curator starting...")
    curator_output = await call_claude(
        CURATOR_SYSTEM,
        f"Crie a versão final para WhatsApp.\n\nTEXTO DO WRITER:\n---\n{writer_output}\n---\n\nFEEDBACK DO CRITIQUE:\n---\n{critique_output}\n---\n\nTEXTO ORIGINAL:\n---\n{raw_text}\n---\n\nProduza APENAS a mensagem formatada.",
    )
    logger.info(f"Curator done ({len(curator_output)} chars)")

    return curator_output


async def run_adjuster(current_draft: str, feedback: str, original_text: str) -> str:
    """Re-run Curator with adjustment feedback."""
    logger.info("Adjuster starting...")
    adjusted = await call_claude(
        ADJUSTER_SYSTEM,
        f"MENSAGEM ATUAL:\n---\n{current_draft}\n---\n\nAJUSTES SOLICITADOS:\n---\n{feedback}\n---\n\nTEXTO ORIGINAL (referência):\n---\n{original_text}\n---\n\nAplique os ajustes e produza a mensagem final.",
    )
    logger.info(f"Adjuster done ({len(adjusted)} chars)")
    return adjusted
