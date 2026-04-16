"""Format a 3-phase pipeline progress display (edited in-place in Telegram).

Phases are Writer → Reviewer → Finalizer. Each call returns the full
message body the caller then passes to edit_message.
"""
from typing import Iterable, Optional

_PHASES = ("Writer", "Reviewer", "Finalizer")
_PHASE_HEADER = {
    "Writer":    "🖋️ *Writer* escrevendo... (1/3)",
    "Reviewer":  "🔍 *Reviewer* analisando... (2/3)",
    "Finalizer": "✨ *Finalizer* polindo... (3/3)",
}
_SEPARATOR = "────────────────────"


def format_pipeline_progress(
    current: Optional[str],
    done: Optional[Iterable[str]] = None,
    error: Optional[str] = None,
) -> str:
    """Build the progress text for a given pipeline state.

    current: the phase currently running (None if all done).
    done: iterable of phases already completed successfully.
    error: if set, marks `current` as failed and later phases as paused.
    """
    done_set = set(done or ())

    # Header line
    if error and current:
        header = f"❌ Erro em *{current}*"
    elif current is None:
        header = "✅ *Draft pronto*"
    else:
        header = _PHASE_HEADER.get(current, f"⏳ *{current}*...")

    # Per-phase status
    lines = [header, _SEPARATOR]
    reached_error = False
    for phase in _PHASES:
        if phase in done_set:
            icon = "✅"
        elif error and phase == current:
            icon = "❌"
            reached_error = True
        elif reached_error:
            icon = "⏸"
        elif current == phase:
            icon = "⏳"
        else:
            icon = "⏳"
        lines.append(f"{icon} {phase}")

    if error:
        lines.append("")
        lines.append(f"_{error}_")

    return "\n".join(lines)
