# Phase 2: Professional UX + Subscriptions + Telegram Delivery

**Date:** 2026-04-16
**Status:** Approved
**Depends on:** Phase 1 (Aiogram 3 migration) completed

## Context

After Phase 1, the bot runs on Aiogram 3 with FSM, CallbackData factories, and async throughout. Phase 2 builds on that foundation to deliver a professional user experience, add a subscription system, and enable Telegram as an additional delivery channel alongside WhatsApp.

## Goals

1. Invite-based access with admin approval flow
2. Professional onboarding for new users with subscription setup wizard
3. Subscription system — on/off per workflow type
4. Telegram delivery channel — workflows send to subscribers alongside WhatsApp
5. Persistent reply keyboard for top-level navigation
6. Consistent formatting, emoji vocabulary, and bot identity
7. Role separation — admin sees everything, subscribers see only their content

## Non-Goals

- Mini App (Phase 3)
- Multi-admin (single admin for now)
- Notification time windows / scheduling
- Telegram-specific formatters (initially reuse WhatsApp format, enhance later)
- Payment / monetization
- Changing WhatsApp delivery — it continues unchanged

## 1. Access Model & Approval

### User Roles

- **Admin** — full access: curation, user approval, workflows, all commands
- **Subscriber** — approved client: receives notifications, manages subscriptions, views reports/news

### Flow

```
Unknown user sends /start
  → Bot: "Olá! Este bot é restrito. Seu pedido de acesso foi
     enviado ao administrador. Você receberá uma notificação
     quando aprovado."
  → Admin receives: "🔔 Novo pedido de acesso: João Silva (@joaosilva)"
     with buttons [✅ Aprovar] [❌ Recusar]
  → Admin approves → user receives full onboarding
  → Admin rejects → user receives "Acesso não autorizado"
```

Already-approved users who send /start see a shorter welcome + main menu.
Admin is pre-seeded with `role: "admin"` — no approval flow needed.

### Data Model

Redis key `user:{chat_id}` as JSON:

```json
{
  "chat_id": 123456,
  "name": "João Silva",
  "username": "joaosilva",
  "role": "subscriber",
  "status": "approved",
  "subscriptions": {
    "morning_check": true,
    "baltic_ingestion": true,
    "daily_report": true,
    "market_news": true,
    "platts_reports": true
  },
  "requested_at": "2026-04-16T12:00:00Z",
  "approved_at": "2026-04-16T12:05:00Z"
}
```

Status values: `pending`, `approved`, `rejected`

### CallbackData

```python
class UserApproval(CallbackData, prefix="user_approve"):
    action: str  # approve, reject
    chat_id: int
```

## 2. Onboarding & Subscriptions

### Onboarding (triggered after approval)

Message 1 — welcome:

```
🥸 *SuperMustache BOT*

Iron ore market intelligence direto no seu Telegram.

O que voce vai receber:
• Precos Platts em tempo real
• Noticias curadas por IA
• Baltic Exchange (BDI + rotas)
• Futuros SGX 62% Fe
• Reports PDF Platts

Vamos configurar o que te interessa?

[⚡ Configurar notificacoes]
```

Tapping "Configurar notificacoes" edits the message to:

```
⚙️ *Notificacoes*

Escolha o que receber:

[✅ Morning Check — Precos Platts]
[✅ Baltic Exchange — BDI + Rotas]
[✅ Daily SGX — Futuros 62% Fe]
[✅ Platts News — Noticias curadas]
[✅ Platts Reports — PDFs]

Toque para ativar/desativar.

[💾 Pronto]
```

Each button is a toggle — tapping edits the message with updated state (✅ ↔ ❌). "Pronto" saves subscriptions and shows the main menu with the reply keyboard.

Default: all subscriptions ON for new users.

### CallbackData

```python
class SubscriptionToggle(CallbackData, prefix="sub_toggle"):
    workflow: str  # morning_check, baltic_ingestion, etc.

class SubscriptionDone(CallbackData, prefix="sub_done"):
    pass
```

### Access Later

`/settings` or "Settings" reply keyboard button opens the same subscription panel for changes at any time.

## 3. Reply Keyboard & Navigation

### Persistent Reply Keyboard

Sent after onboarding completes and re-sent with `/menu`. Fixed at bottom of screen, replaces phone keyboard:

```
┌──────────────┬──────────────┐
│ 📊 Reports   │ 📰 Fila      │
├──────────────┼──────────────┤
│ ⚡ Workflows  │ ⚙️ Settings  │
└──────────────┴──────────────┘
```

4 buttons, 2x2 grid. Each sends text as a regular message — bot treats it like a command. Always accessible, never clutters chat.

Implementation: `ReplyKeyboardMarkup` with `resize_keyboard=True` and `is_persistent=True`.

### Two Keyboard Types Working Together

- **Reply keyboard** (bottom of screen) → primary navigation. Always visible. User never needs to type commands.
- **Inline keyboard** (inside messages) → contextual actions. Report navigation, subscription toggles, workflow trigger, approve/reject. Edits message in place.

### Command Mapping

| Reply Keyboard | Equivalent Command | Behavior |
|---|---|---|
| 📊 Reports | `/reports` | Opens report navigation |
| 📰 Fila | `/queue` | Shows items pending curation |
| ⚡ Workflows | `/workflows` | Workflow list with trigger |
| ⚙️ Settings | `/settings` | Subscriptions + preferences |

### Admin vs Subscriber

- **Subscriber** sees: reply keyboard (4 buttons above) + their subscribed content
- **Admin** sees: same reply keyboard + `/s` inline menu with admin functions (history, rejections, stats, status, contacts, reprocess, add contact, help)

Subscribers don't see admin-only commands in autocomplete. Register different command sets per user role via `setMyCommands` with scope.

## 4. Telegram Delivery

### Current Flow (unchanged)

```
GitHub Actions → execution script → POST /store-draft → WhatsApp (UAZAPI)
```

### New Flow (additional)

```
GitHub Actions → execution script → POST /store-draft → WhatsApp (UAZAPI)
                                                       → Telegram (subscribers)
```

### Payload Changes

`/store-draft` receives two new fields:

```json
{
  "draft_id": "morning_check_20260416_0830",
  "message": "formatted content...",
  "workflow_type": "morning_check",
  "direct_delivery": true
}
```

- `workflow_type` — maps to subscription key
- `direct_delivery: true` — content is ready, send without admin approval

### Delivery Logic

When `direct_delivery: true`:
1. Send to WhatsApp normally (existing flow, unchanged)
2. Query Redis for all `user:*` with `status: "approved"` AND `subscriptions[workflow_type]: true`
3. Send to each subscriber via Telegram
4. Log delivery results

When `direct_delivery: false` or absent:
- Current behavior (approval card to admin)

### Execution Script Changes

Each script in `execution/scripts/` adds 2 fields to the `/store-draft` POST payload (~2 lines per script, 5 scripts total):

```python
payload = {
    "draft_id": draft_id,
    "message": formatted_message,
    "workflow_type": "morning_check",  # NEW
    "direct_delivery": True,           # NEW
}
```

### Telegram Formatting

Initially reuse the same WhatsApp-formatted message (already works with Markdown). Telegram-specific rich formatting is an incremental enhancement — not a blocker for delivery.

Future enhancement example:
```
📊 *MORNING CHECK*
_16/04/2026 — 08:30 BRT_

`Brazilian Blend Fines   $108.50`
`Jimblebar Fines         $106.75`
`IODEX 62% Fe            $107.25`
`Pellet Premium          $32.00`

▲ $1.25 vs dia anterior
```

## 5. Formatting & Identity

### Consistent Emoji Vocabulary

All bot messages use the same mapping:

| Emoji | Meaning |
|---|---|
| ✅ | Success, active, approved |
| ❌ | Failure, inactive, rejected |
| 🔄 | In progress, running |
| ⏳ | Waiting, pending |
| 📊 | Data, reports, prices |
| 📰 | News, content |
| ⚡ | Workflows, actions |
| ⚙️ | Settings |
| 🔔 | Notifications, alerts |
| 🥸 | Bot identity |

### Golden Rule: Edit, Never Send New

Every inline keyboard interaction edits the existing message. Never send a new message in response to a button press. Audit all handlers to enforce this.

### Bot Identity (BotFather)

- **Name:** SuperMustache BOT
- **About:** Iron ore market intelligence — prices, news, reports
- **Description:** Preços Platts, notícias curadas por IA, Baltic Exchange, futuros SGX e reports PDF direto no seu Telegram.
- **Photo:** set manually

### Error Messages

Consistent pattern for subscribers:
```
⚠️ *Erro*
{short description}

Tente novamente ou contate o admin.
```

Never show stack traces or technical details to subscribers.

## New Modules

- `webhook/bot/routers/onboarding.py` — /start flow, access request, approval handling
- `webhook/bot/routers/settings.py` — /settings, subscription management
- `webhook/bot/delivery.py` — Telegram delivery to subscribers
- `webhook/bot/users.py` — User CRUD (Redis), role checks, subscription queries
- `webhook/bot/keyboards.py` — Reply keyboard builder (add to existing)

## Modified Modules

- `webhook/bot/main.py` — register new routers, send reply keyboard on /start
- `webhook/bot/callbacks.py` — add UserApproval, SubscriptionToggle, SubscriptionDone CallbackData
- `webhook/bot/middlewares/auth.py` — role-aware: admin vs subscriber vs unknown
- `webhook/bot/routers/commands.py` — update /s menu, add /settings, /menu
- `webhook/routes/api.py` — update /store-draft to accept workflow_type + direct_delivery, trigger Telegram delivery
- `execution/scripts/*.py` — add workflow_type and direct_delivery to /store-draft payloads (5 files, ~2 lines each)

## Redis Keys (new)

- `user:{chat_id}` — user profile + subscriptions (JSON, no TTL — persistent)

## Testing Strategy

- Unit tests for user CRUD (create pending, approve, reject, get subscribers)
- Unit tests for subscription toggle (on/off, defaults)
- Unit tests for delivery routing (only subscribed users receive)
- FSM tests for onboarding flow (unknown → pending → approved → onboarding)
- FSM tests for subscription toggle interaction
- Test reply keyboard is sent after onboarding
- Test /store-draft with direct_delivery triggers Telegram delivery
- Test admin receives approval request when unknown user sends /start
- Test subscriber cannot access admin commands

## Success Criteria

1. Unknown user sends /start → gets "pending" message, admin gets approval card
2. Admin approves → user receives onboarding with subscription wizard
3. Subscription toggles work (edit message in place, persist in Redis)
4. Reply keyboard appears after onboarding and persists
5. Workflows with direct_delivery send to WhatsApp AND subscribed Telegram users
6. Subscriber sees only their allowed features (no admin commands)
7. All inline keyboard interactions edit existing messages (no chat spam)
8. Consistent emoji vocabulary across all messages
