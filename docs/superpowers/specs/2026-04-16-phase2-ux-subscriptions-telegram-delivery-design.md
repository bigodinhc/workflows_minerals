# Phase 2: Professional UX + Subscriptions + Telegram Delivery

**Date:** 2026-04-16
**Status:** Approved
**Depends on:** Phase 1 (Aiogram 3 migration) completed

## Context

After Phase 1, the bot runs on Aiogram 3 with FSM, CallbackData factories, and async throughout. Phase 2 builds on that foundation to deliver a professional user experience following the best practices from the Telegram Bots research document.

The bot currently serves one admin user. This phase adds:
1. Professional UX patterns (onboarding, navigation, formatting)
2. Subscription system (choose which content types to receive)
3. Telegram as an additional delivery channel (alongside WhatsApp)

## Goals

1. Professional onboarding flow for new users (even if "new user" is just you for now)
2. Subscription preferences — select which of the 5 workflows to receive via Telegram
3. Telegram delivery channel — workflows send content to Telegram subscribers in addition to WhatsApp
4. Improved message formatting following Telegram best practices
5. Reply keyboard for persistent navigation (main menu always accessible)
6. Clean inline keyboard interactions (edit messages, never spam)
7. Bot identity — name, avatar, description, personality

## Non-Goals

- Mini App (that's Phase 3)
- Multi-tenant admin (multiple admins curating content)
- Payment/monetization
- Public bot discovery — bot stays private for now
- Changing WhatsApp delivery — it continues working exactly as today

## Features

### 1. Bot Identity & Onboarding

**Bot profile:**
- Name: SuperMustache BOT (already set)
- Description: "Iron ore market intelligence — prices, news, reports, analytics"
- About text: short explanation visible before /start
- Profile photo: to be set manually via BotFather

**Onboarding flow (triggered by /start):**

```
Welcome message:
┌────────────────────────────────────┐
│ 🥸 SuperMustache BOT               │
│                                    │
│ Iron ore market intelligence.      │
│                                    │
│ O que eu faço:                     │
│ • Precos Platts em tempo real      │
│ • Noticias curadas por IA          │
│ • Baltic Exchange (BDI + rotas)    │
│ • Futuros SGX 62% Fe              │
│ • Reports PDF Platts               │
│                                    │
│ [⚡ Configurar notificacoes]       │
│ [📊 Ver menu completo]             │
└────────────────────────────────────┘
```

- "Configurar notificacoes" → subscription setup flow
- "Ver menu completo" → main menu (same as /s today)

For returning users, /start shows a shorter welcome + main menu.

### 2. Subscription System

**Data model:**

Stored in Redis with key `sub:{chat_id}` as JSON:

```json
{
  "chat_id": 8375309778,
  "subscriptions": {
    "morning_check": true,
    "baltic_ingestion": true,
    "daily_report": true,
    "market_news": true,
    "platts_reports": false
  },
  "created_at": "2026-04-16T12:00:00Z",
  "updated_at": "2026-04-16T14:30:00Z"
}
```

Default: all subscriptions ON for new users.

**Subscription management UI:**

Accessible via /settings or "Configurar notificacoes" button:

```
⚙️ *Notificacoes*

Escolha o que receber no Telegram:

[✅ Morning Check — Precos Platts]
[✅ Baltic Exchange — BDI + Rotas]
[✅ Daily SGX — Futuros 62% Fe]
[✅ Platts News — Noticias curadas]
[❌ Platts Reports — PDFs]

[⬅ Menu]
```

Each button is a toggle — tap to flip on/off. The message edits in place showing the updated state. Uses CallbackData:

```python
class SubscriptionToggle(CallbackData, prefix="sub_toggle"):
    workflow: str  # morning_check, baltic_ingestion, etc.
```

### 3. Telegram Delivery Channel

**How it works:**

When a GitHub Actions workflow completes and sends content to WhatsApp, it ALSO sends to Telegram subscribers. The delivery flow:

```
GitHub Actions workflow runs
  → execution script produces formatted message
  → POST /store-draft to webhook (existing endpoint)
  → webhook stores draft in Redis
  → webhook sends to WhatsApp (existing flow, unchanged)
  → NEW: webhook queries Redis for subscribers of this workflow type
  → NEW: webhook sends formatted message to each subscribed chat_id via Telegram
```

**Implementation:**

New module `webhook/bot/delivery.py`:

```python
async def deliver_to_telegram_subscribers(workflow_type: str, message: str, bot: Bot):
    """Send content to all Telegram subscribers of this workflow type."""
    subscribers = await get_subscribers_for_workflow(workflow_type)
    for chat_id in subscribers:
        try:
            await bot.send_message(chat_id, message, parse_mode="Markdown")
        except Exception as exc:
            logger.error(f"Telegram delivery failed for {chat_id}: {exc}")
```

**Trigger point:**

The `/store-draft` endpoint currently stores the draft and optionally sends an approval card. For automated workflows (morning_check, daily_report, baltic_ingestion), the content is already approved — it should go directly to subscribers without manual approval.

Add a `direct_delivery` flag to the `/store-draft` payload:

```json
{
  "draft_id": "morning_check_20260416_0830",
  "message": "formatted content...",
  "workflow_type": "morning_check",
  "direct_delivery": true
}
```

When `direct_delivery: true`:
- Still sends to WhatsApp (existing behavior)
- Also sends to Telegram subscribers of that workflow_type
- No approval card needed

When `direct_delivery: false` (or absent):
- Current behavior (store draft, show approval card to admin)

**Message formatting for Telegram:**

Telegram supports richer formatting than WhatsApp. Create Telegram-specific formatters that enhance the existing WhatsApp messages:

```python
# webhook/bot/formatters.py

def format_morning_check_telegram(data: dict) -> str:
    """Format morning check prices for Telegram (richer than WhatsApp version)."""
    return (
        "📊 *MORNING CHECK*\n"
        f"_{data['date']}_\n\n"
        f"Brazilian Blend Fines: `${data['fines']}`\n"
        f"Jimblebar Fines: `${data['jimblebar']}`\n"
        f"IODEX 62% Fe: `${data['iodex']}`\n"
        f"Pellet Premium: `${data['pellet']}`\n"
        # ... etc
    )
```

Initially, reuse the same WhatsApp-formatted message for Telegram (it already works). Telegram-specific formatting is a nice-to-have enhancement, not a blocker.

### 4. Persistent Reply Keyboard

Add a reply keyboard (bottom of screen, replaces phone keyboard) for always-accessible navigation:

```
┌─────────────┬──────────────┐
│ 📊 Reports  │ 📰 News      │
├─────────────┼──────────────┤
│ ⚡ Workflows │ ⚙️ Settings  │
└─────────────┴──────────────┘
```

This is a `ReplyKeyboardMarkup` — persists on screen, doesn't clutter the chat. Sent once on /start and re-sent if user types /menu.

The inline keyboards (current system) continue for contextual actions within messages. The reply keyboard provides top-level navigation.

### 5. Improved Message Formatting

Apply formatting best practices from the research document:

**Hierarchy in messages:**
- Title in bold
- Key data in `inline code` (mono font, gray background)
- Timestamps in _italic_
- Section dividers with blank lines
- Status indicators with consistent emoji vocabulary

**Emoji vocabulary (consistent across all messages):**
```
✅ Success / Active / Approved
❌ Failure / Inactive / Rejected
🔄 In progress / Running
⏳ Waiting / Pending
📊 Data / Reports / Prices
📰 News / Content
⚡ Workflows / Actions
⚙️ Settings / Config
🥸 Bot identity
```

**Edit messages instead of sending new ones:**
Already implemented for most interactions. Audit remaining cases where new messages are sent unnecessarily and convert to edits.

### 6. /settings Command

New command that centralizes user preferences:

```
⚙️ *Settings*

[📩 Notificacoes]     — escolher o que receber
[🕐 Horarios]         — (futuro) definir janela de notificacao
[📋 Sobre]            — informacoes do bot

[⬅ Menu]
```

For now, only "Notificacoes" is functional. "Horarios" shows "Em breve" toast. "Sobre" shows bot version, uptime, and contact info.

### 7. Updated /s Main Menu

Restructure the main menu to be cleaner:

```
🥸 *SuperMustache BOT*

━━ Mercado ━━
[📊 Reports] [📰 Fila]

━━ Dados ━━
[📜 Historico] [📈 Stats]

━━ Sistema ━━
[⚡ Workflows] [🔄 Status]

━━ Admin ━━
[📋 Contatos] [⚙️ Settings]
```

Groups related actions visually. Uses section headers in the message text.

## Data Flow: End-to-End Telegram Delivery

```
┌──────────────────────────────────────────────┐
│ GitHub Actions: morning_check.yml            │
│                                              │
│ 1. Run execution/scripts/morning_check.py    │
│ 2. Produce formatted message                 │
│ 3. POST /store-draft with:                   │
│    - message, workflow_type: "morning_check"  │
│    - direct_delivery: true                   │
└──────────────────┬───────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────┐
│ Webhook: /store-draft handler                │
│                                              │
│ 1. Store draft in Redis                      │
│ 2. If direct_delivery:                       │
│    a. Send to WhatsApp (existing)            │
│    b. Query sub:* keys for subscribers       │
│       where subscriptions[workflow_type]=true │
│    c. Send Telegram message to each          │
│ 3. If not direct_delivery:                   │
│    a. Send approval card to admin (existing) │
└──────────────────────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────┐
│ Telegram: User receives notification         │
│                                              │
│ 📊 *MORNING CHECK*                           │
│ _16/04/2026 08:30 BRT_                       │
│                                              │
│ Brazilian Blend Fines: `$108.50`             │
│ IODEX 62% Fe: `$107.25`                     │
│ ...                                          │
└──────────────────────────────────────────────┘
```

## Technical Details

### New modules

- `webhook/bot/delivery.py` — Telegram delivery to subscribers
- `webhook/bot/formatters.py` — Telegram-specific message formatting (optional enhancement)
- `webhook/bot/routers/settings.py` — /settings command + subscription management handlers
- `webhook/bot/routers/onboarding.py` — /start onboarding flow

### Modified modules

- `webhook/bot/main.py` — register new routers, add reply keyboard on /start
- `webhook/bot/callbacks.py` — add SubscriptionToggle CallbackData
- `webhook/bot/keyboards.py` — add reply keyboard, subscription keyboard builders
- `webhook/routes/api.py` — update /store-draft to trigger Telegram delivery
- `webhook/bot/routers/commands.py` — update /s menu structure, add /settings, /menu

### Redis keys (new)

- `sub:{chat_id}` — subscription preferences (JSON, no TTL — persistent)

### Execution scripts (minor changes)

The GitHub Actions scripts need to pass `workflow_type` and `direct_delivery: true` in the `/store-draft` POST payload. This is a 2-line change per script:

```python
# In each execution script's send_to_webhook() call:
payload = {
    "draft_id": draft_id,
    "message": formatted_message,
    "workflow_type": "morning_check",  # NEW
    "direct_delivery": True,           # NEW
}
requests.post(f"{WEBHOOK_URL}/store-draft", json=payload)
```

## Testing Strategy

- Unit tests for subscription CRUD (get/set/toggle)
- Unit tests for delivery routing (only subscribed users receive)
- Unit tests for reply keyboard generation
- FSM tests for onboarding flow (new user vs returning user)
- FSM tests for subscription toggle flow
- Integration test: /store-draft with direct_delivery triggers Telegram sends
- Manual test: full flow from GitHub Actions → WhatsApp + Telegram delivery

## Success Criteria

1. /start shows professional onboarding for new users
2. /settings allows toggling subscriptions per workflow type
3. Workflows with direct_delivery send to Telegram subscribers AND WhatsApp
4. Reply keyboard provides persistent bottom navigation
5. Messages use consistent formatting and emoji vocabulary
6. All interactions edit existing messages (no chat spam)
7. Subscription state persists in Redis across redeploys
