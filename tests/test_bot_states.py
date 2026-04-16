"""Tests for FSM state definitions."""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

from aiogram.fsm.state import StatesGroup, State
from bot.states import AdjustDraft, RejectReason, AddContact, NewsInput


def test_adjust_draft_has_waiting_feedback():
    assert hasattr(AdjustDraft, "waiting_feedback")
    assert isinstance(AdjustDraft.waiting_feedback, State)


def test_reject_reason_has_waiting_reason():
    assert hasattr(RejectReason, "waiting_reason")
    assert isinstance(RejectReason.waiting_reason, State)


def test_add_contact_has_waiting_data():
    assert hasattr(AddContact, "waiting_data")
    assert isinstance(AddContact.waiting_data, State)


def test_news_input_has_processing():
    assert hasattr(NewsInput, "processing")
    assert isinstance(NewsInput.processing, State)


def test_all_are_states_groups():
    for cls in (AdjustDraft, RejectReason, AddContact, NewsInput):
        assert issubclass(cls, StatesGroup)
