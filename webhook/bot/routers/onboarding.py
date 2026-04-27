"""Onboarding flow: /start for unknown users, admin approval, welcome wizard.

Public router (no middleware) — handles all /start regardless of role.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

from bot.config import get_bot, TELEGRAM_CHAT_ID
from bot.callback_data import UserApproval, OnboardingStart, SubscriptionToggle, SubscriptionDone
from bot.keyboards import (
    build_reply_keyboard, build_approval_request_keyboard,
    build_onboarding_keyboard, build_subscription_keyboard,
)
from bot.users import (
    get_user, create_pending_user, approve_user, reject_user,
    get_user_role, is_admin, is_onedrive_approver, toggle_subscription,
)

logger = logging.getLogger(__name__)

onboarding_router = Router(name="onboarding")


# ── /start command (public, no middleware) ──

@onboarding_router.message(Command("start"))
async def cmd_start(message: Message):
    chat_id = message.chat.id
    role = get_user_role(chat_id)

    if role == "admin":
        await message.answer(
            "🥸 *SuperMustache BOT*\n\nBem vindo, admin.",
            reply_markup=build_reply_keyboard(is_admin=True),
        )
        return

    if role == "subscriber":
        await message.answer(
            "🥸 *SuperMustache BOT*\n\nBem vindo de volta!",
            reply_markup=build_reply_keyboard(),
        )
        return

    if role == "pending":
        await message.answer(
            "⏳ Seu pedido de acesso ainda esta em analise.\n"
            "Voce recebera uma notificacao quando aprovado.",
        )
        return

    # OneDrive approver who isn't admin/subscriber/pending — fixed welcome,
    # no pending record created. They interact only via approval card buttons.
    if is_onedrive_approver(chat_id) and not is_admin(chat_id) and get_user(chat_id) is None:
        await message.answer(
            "👋 Olá! Você está cadastrado como aprovador de relatórios "
            "OneDrive.\n\nEu vou te enviar os PDFs novos da pasta SharePoint "
            "assim que chegarem. Use os botões de cada card pra aprovar ou "
            "descartar.",
        )
        return

    # Unknown user — create pending + notify admin (existing behavior)
    user = message.from_user
    name = user.full_name or "Desconhecido"
    username = user.username or ""
    create_pending_user(chat_id=chat_id, name=name, username=username)

    await message.answer(
        "Ola! Este bot e restrito.\n\n"
        "Seu pedido de acesso foi enviado ao administrador.\n"
        "Voce recebera uma notificacao quando aprovado.",
    )

    bot = get_bot()
    admin_id = int(TELEGRAM_CHAT_ID) if TELEGRAM_CHAT_ID.isdigit() else 0
    if admin_id:
        mention = f"@{username}" if username else name
        await bot.send_message(
            admin_id,
            f"🔔 *Novo pedido de acesso*\n\n"
            f"Nome: {name}\n"
            f"User: {mention}\n"
            f"ID: `{chat_id}`",
            reply_markup=build_approval_request_keyboard(chat_id),
        )
    logger.info(f"Access request from {chat_id} ({name})")


# ── Admin approval/rejection callbacks ──

@onboarding_router.callback_query(UserApproval.filter())
async def on_user_approval(query: CallbackQuery, callback_data: UserApproval):
    requester_id = query.from_user.id
    if not is_admin(requester_id):
        await query.answer("Nao autorizado")
        return

    target_chat_id = callback_data.chat_id
    action = callback_data.action
    bot = get_bot()

    if action == "approve":
        user = approve_user(target_chat_id)
        if user is None:
            await query.answer("Usuario nao encontrado")
            return
        await query.answer("✅ Aprovado")

        # Update admin message
        await bot.edit_message_text(
            f"✅ *Aprovado* — {user['name']}",
            chat_id=query.message.chat.id,
            message_id=query.message.message_id,
            reply_markup=None,
        )

        # Send onboarding to the approved user
        await bot.send_message(
            target_chat_id,
            "🥸 *SuperMustache BOT*\n\n"
            "Iron ore market intelligence direto no seu Telegram.\n\n"
            "O que voce vai receber:\n"
            "• Precos Platts em tempo real\n"
            "• Noticias curadas por IA\n"
            "• Baltic Exchange (BDI + rotas)\n"
            "• Futuros SGX 62% Fe\n"
            "• Reports PDF Platts\n\n"
            "Vamos configurar o que te interessa?",
            reply_markup=build_onboarding_keyboard(),
        )
        logger.info(f"User {target_chat_id} approved")

    elif action == "reject":
        user = reject_user(target_chat_id)
        if user is None:
            await query.answer("Usuario nao encontrado")
            return
        await query.answer("❌ Recusado")

        await bot.edit_message_text(
            f"❌ *Recusado* — {user['name']}",
            chat_id=query.message.chat.id,
            message_id=query.message.message_id,
            reply_markup=None,
        )

        await bot.send_message(target_chat_id, "Acesso nao autorizado.")
        logger.info(f"User {target_chat_id} rejected")


# ── Onboarding: "Configurar notificacoes" button ──

@onboarding_router.callback_query(OnboardingStart.filter())
async def on_onboarding_start(query: CallbackQuery):
    user = get_user(query.from_user.id)
    if user is None:
        await query.answer("Erro")
        return
    subs = user.get("subscriptions", {})
    await query.answer("")
    bot = get_bot()
    await bot.edit_message_text(
        "⚙️ *Notificacoes*\n\n"
        "Escolha o que receber:\n\n"
        "Toque para ativar/desativar.",
        chat_id=query.message.chat.id,
        message_id=query.message.message_id,
        reply_markup=build_subscription_keyboard(subs),
    )


# ── Subscription toggle ──

@onboarding_router.callback_query(SubscriptionToggle.filter())
async def on_subscription_toggle(query: CallbackQuery, callback_data: SubscriptionToggle):
    chat_id = query.from_user.id
    toggle_subscription(chat_id, callback_data.workflow)
    user = get_user(chat_id)
    if user is None:
        await query.answer("Erro")
        return
    subs = user.get("subscriptions", {})
    await query.answer("")
    bot = get_bot()
    await bot.edit_message_reply_markup(
        chat_id=query.message.chat.id,
        message_id=query.message.message_id,
        reply_markup=build_subscription_keyboard(subs),
    )


# ── Subscription done ──

@onboarding_router.callback_query(SubscriptionDone.filter())
async def on_subscription_done(query: CallbackQuery):
    user = get_user(query.from_user.id)
    if user is None:
        await query.answer("Erro")
        return
    active = sum(1 for v in user.get("subscriptions", {}).values() if v)
    await query.answer("💾 Salvo!")
    bot = get_bot()
    await bot.edit_message_text(
        f"✅ *Configuracao salva*\n\n{active} notificacoes ativas.",
        chat_id=query.message.chat.id,
        message_id=query.message.message_id,
        reply_markup=None,
    )
    # Send reply keyboard
    await bot.send_message(
        query.from_user.id,
        "Use os botoes abaixo para navegar.",
        reply_markup=build_reply_keyboard(),
    )
