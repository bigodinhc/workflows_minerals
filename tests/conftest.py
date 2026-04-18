"""Shared pytest fixtures."""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
# Add repo root to sys.path so tests can import execution.* modules
sys.path.insert(0, str(_REPO_ROOT))
# Add webhook/ so bare imports (`import redis_queries`) resolve the same way
# they do in production (Dockerfile copies webhook/ contents to /app/).
sys.path.insert(0, str(_REPO_ROOT / "webhook"))


# ─── Shared fixtures for router tests (Phase 1 safety net) ───────────────────
from unittest.mock import AsyncMock, MagicMock
import pytest
from aiogram import Bot
from aiogram.types import CallbackQuery, Message, Chat, User
from aiogram.fsm.context import FSMContext


@pytest.fixture
def mock_bot():
    """AsyncMock of aiogram Bot with the methods callback/message handlers call."""
    bot = AsyncMock(spec=Bot)
    bot.send_message = AsyncMock()
    bot.edit_message_text = AsyncMock()
    bot.answer_callback_query = AsyncMock()
    return bot


@pytest.fixture
def mock_callback_query():
    """Factory: mock_callback_query(user_id=12345, chat_id=12345, message_id=1, data='...')."""
    def _factory(user_id: int = 12345, chat_id: int = 12345,
                 message_id: int = 1, data: str = ""):
        cb = MagicMock(spec=CallbackQuery)
        cb.id = "cb_test_id"
        cb.data = data
        cb.from_user = MagicMock(spec=User)
        cb.from_user.id = user_id
        cb.from_user.first_name = "Test"
        cb.message = MagicMock(spec=Message)
        cb.message.message_id = message_id
        cb.message.chat = MagicMock(spec=Chat)
        cb.message.chat.id = chat_id
        cb.message.answer = AsyncMock()
        cb.answer = AsyncMock()
        return cb
    return _factory


@pytest.fixture
def mock_message():
    """Factory: mock_message(text='hi', chat_id=12345, user_id=12345)."""
    def _factory(text: str = "", chat_id: int = 12345, user_id: int = 12345):
        msg = MagicMock(spec=Message)
        msg.text = text
        msg.message_id = 1
        msg.chat = MagicMock(spec=Chat)
        msg.chat.id = chat_id
        msg.from_user = MagicMock(spec=User)
        msg.from_user.id = user_id
        msg.answer = AsyncMock()
        return msg
    return _factory


@pytest.fixture
def fsm_context_in_state():
    """Factory: fsm_context_in_state(state=AdjustDraft.waiting_feedback, data={'draft_id': 'x'})."""
    def _factory(state=None, data: dict | None = None):
        ctx = MagicMock(spec=FSMContext)
        ctx.get_state = AsyncMock(return_value=state)
        ctx.get_data = AsyncMock(return_value=data or {})
        ctx.set_state = AsyncMock()
        ctx.update_data = AsyncMock()
        ctx.clear = AsyncMock()
        return ctx
    return _factory
