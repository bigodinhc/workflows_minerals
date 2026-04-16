"""Inline keyboard builders.

Centralizes keyboard construction so routers stay lean.
Uses InlineKeyboardBuilder from aiogram.utils.keyboard.
"""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.callback_data import (
    DraftAction, MenuAction,
    ReportType as ReportTypeCB, ReportYears, ReportBack,
    WorkflowList,
)


def build_approval_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    """Build the 4-button approval keyboard for a draft."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Aprovar e Enviar",
            callback_data=DraftAction(action="approve", draft_id=draft_id).pack(),
        ),
        InlineKeyboardButton(
            text="🧪 Teste",
            callback_data=DraftAction(action="test_approve", draft_id=draft_id).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="✏️ Ajustar",
            callback_data=DraftAction(action="adjust", draft_id=draft_id).pack(),
        ),
        InlineKeyboardButton(
            text="❌ Rejeitar",
            callback_data=DraftAction(action="reject", draft_id=draft_id).pack(),
        ),
    )
    return builder.as_markup()


def build_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Build the /s main menu keyboard."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📊 Relatórios", callback_data=MenuAction(target="reports").pack()),
        InlineKeyboardButton(text="📰 Fila", callback_data=MenuAction(target="queue").pack()),
    )
    builder.row(
        InlineKeyboardButton(text="📜 Histórico", callback_data=MenuAction(target="history").pack()),
        InlineKeyboardButton(text="❌ Recusados", callback_data=MenuAction(target="rejections").pack()),
    )
    builder.row(
        InlineKeyboardButton(text="📈 Stats", callback_data=MenuAction(target="stats").pack()),
        InlineKeyboardButton(text="🔄 Status", callback_data=MenuAction(target="status").pack()),
    )
    builder.row(
        InlineKeyboardButton(text="🔁 Reprocessar", callback_data=MenuAction(target="reprocess").pack()),
        InlineKeyboardButton(text="📋 Contatos", callback_data=MenuAction(target="list").pack()),
    )
    builder.row(
        InlineKeyboardButton(text="➕ Add Contato", callback_data=MenuAction(target="add").pack()),
    )
    builder.row(
        InlineKeyboardButton(text="⚡ Workflows", callback_data=WorkflowList(action="list").pack()),
        InlineKeyboardButton(text="❓ Help", callback_data=MenuAction(target="help").pack()),
    )
    return builder.as_markup()


def build_report_types_keyboard() -> InlineKeyboardMarkup:
    """Build the report category selection keyboard."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="📊 Market Reports",
        callback_data=ReportTypeCB(report_type="Market Reports").pack(),
    ))
    builder.row(InlineKeyboardButton(
        text="📊 Research Reports",
        callback_data=ReportTypeCB(report_type="Research Reports").pack(),
    ))
    return builder.as_markup()
