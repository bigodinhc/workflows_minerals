"""Inline keyboard builders.

Centralizes keyboard construction so routers stay lean.
Uses InlineKeyboardBuilder from aiogram.utils.keyboard.
"""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.callback_data import (
    DraftAction, MenuAction,
    ReportType as ReportTypeCB, ReportYears, ReportBack,
    WorkflowList,
    UserApproval, SubscriptionToggle, SubscriptionDone, OnboardingStart,
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
        InlineKeyboardButton(text="🖋️ Writer", callback_data=MenuAction(target="writer").pack()),
    )
    builder.row(
        InlineKeyboardButton(text="📲 Enviar Msg", callback_data=MenuAction(target="broadcast").pack()),
        InlineKeyboardButton(text="❓ Help", callback_data=MenuAction(target="help").pack()),
    )
    builder.row(
        InlineKeyboardButton(text="⚡ Workflows", callback_data=WorkflowList(action="list").pack()),
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


def build_reply_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    """Build the persistent reply keyboard for bottom navigation.

    Admin gets a 5th button (3rd row) to open the admin menu.
    """
    rows = [
        [KeyboardButton(text="📊 Reports"), KeyboardButton(text="📰 Fila")],
        [KeyboardButton(text="⚡ Workflows"), KeyboardButton(text="⚙️ Settings")],
    ]
    if is_admin:
        rows.append([KeyboardButton(text="🖋️ Writer"), KeyboardButton(text="📲 Enviar Msg")])
        rows.append([KeyboardButton(text="🥸 Admin")])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
    )


def build_onboarding_keyboard() -> InlineKeyboardMarkup:
    """Build the onboarding welcome keyboard."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="⚡ Configurar notificacoes",
        callback_data=OnboardingStart().pack(),
    ))
    return builder.as_markup()


def build_subscription_keyboard(subscriptions: dict) -> InlineKeyboardMarkup:
    """Build the subscription toggle panel.

    subscriptions: dict like {"morning_check": True, "baltic_ingestion": False, ...}
    """
    labels = {
        "morning_check": "Morning Check — Precos Platts",
        "baltic_ingestion": "Baltic Exchange — BDI + Rotas",
        "daily_report": "Daily SGX — Futuros 62% Fe",
        "market_news": "Platts News — Noticias curadas",
        "platts_reports": "Platts Reports — PDFs",
    }
    builder = InlineKeyboardBuilder()
    for wf, label in labels.items():
        active = subscriptions.get(wf, True)
        icon = "✅" if active else "❌"
        builder.row(InlineKeyboardButton(
            text=f"{icon} {label}",
            callback_data=SubscriptionToggle(workflow=wf).pack(),
        ))
    builder.row(InlineKeyboardButton(
        text="💾 Pronto",
        callback_data=SubscriptionDone().pack(),
    ))
    return builder.as_markup()


def build_approval_request_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    """Build the admin approval request keyboard."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Aprovar",
            callback_data=UserApproval(action="approve", chat_id=chat_id).pack(),
        ),
        InlineKeyboardButton(
            text="❌ Recusar",
            callback_data=UserApproval(action="reject", chat_id=chat_id).pack(),
        ),
    )
    return builder.as_markup()
