"""Agent system prompts: Writer → Critique → Curator pipeline + post-send Adjuster."""
from execution.core.prompts.writer import WRITER_SYSTEM
from execution.core.prompts.critique import CRITIQUE_SYSTEM
from execution.core.prompts.curator import CURATOR_SYSTEM
from execution.core.prompts.adjuster import ADJUSTER_SYSTEM

__all__ = ["WRITER_SYSTEM", "CRITIQUE_SYSTEM", "CURATOR_SYSTEM", "ADJUSTER_SYSTEM"]
