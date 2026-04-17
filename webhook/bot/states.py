"""FSM StatesGroup definitions.

Replace in-memory dicts (ADJUST_STATE, REJECT_REASON_STATE, ADMIN_STATE)
with Aiogram FSM backed by RedisStorage — state survives redeploys.
"""

from aiogram.fsm.state import State, StatesGroup


class AdjustDraft(StatesGroup):
    """User pressed 'Ajustar' and must send feedback text."""
    waiting_feedback = State()


class RejectReason(StatesGroup):
    """User pressed 'Rejeitar' — optionally sends a reason."""
    waiting_reason = State()


class AddContact(StatesGroup):
    """User typed /add — must send 'Nome Telefone'."""
    waiting_data = State()


class NewsInput(StatesGroup):
    """Guard: text is being processed by the 3-agent pipeline."""
    processing = State()


class BroadcastMessage(StatesGroup):
    """Admin wants to send a free-form message to WhatsApp contacts."""
    waiting_text = State()
