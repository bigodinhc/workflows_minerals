# Design: Distribuição "full Telegram" — canal privado para relatórios de cliente

- **Data:** 2026-07-09
- **Status:** Implementado (plano: docs/superpowers/plans/2026-07-09-telegram-channel-delivery.md); pendente rollout manual (§7)
- **Autor:** brainstorming colaborativo (usuário + Claude)

## 1. Problema & objetivo

A distribuição dos relatórios de mercado para clientes dependia de um gateway **não-oficial
de WhatsApp (uazapi/Baileys)**. Dois números foram **banidos/restringidos** em sequência por
disparo em massa a partir de chip novo sem aquecimento (ver [[whatsapp-ban-incident]]). O
problema é estrutural: automação de WhatsApp Web via gateway não-oficial é violação de ToS e
é caçada pelo anti-spam do Meta. Nenhum ajuste de delay/spintax elimina o risco.

O objetivo é **abandonar o WhatsApp** e migrar a distribuição de relatórios de cliente para
o **Telegram**, que é canal nativo/sancionado (não banível como spam) e que o projeto **já
usa** (bot aiogram 3, store de assinantes no Redis, Mini App, curadoria/aprovação). A meta é
previsibilidade de entrega + ganhos de recurso (PDF nativo, formatação rica, analytics de
view, arquivo histórico).

### Decisões travadas (do brainstorming)

1. **Modelo:** **canal privado** como meio principal de broadcast do relatório diário —
   **só recebimento** (one-way). Dúvidas de cliente vão por outro caminho (DM ao bot/admin),
   sem grupo de discussão no v1.
2. **Escopo de conteúdo:** **1 canal** que recebe apenas **conteúdo de cliente**
   (`daily_report`, `market_news`, `platts_reports`). Workflows internos/operacionais
   (`morning_check`, `baltic_ingestion`) permanecem no chat/admin interno como hoje.
3. **Migração dos ~74 contatos:** **link de convite com aprovação** (join request aprovado
   pelo admin, reusando o fluxo de aprovação existente), com expiração e limite de joins +
   **QR** do link. Divulgação por e-mail/ligação (WhatsApp está restrito).
4. **Aposentar o uazapi para cliente:** desligar o broadcast uazapi dos relatórios de
   cliente via flag; o código permanece mas inativo. Sem chip, sem ban.
5. **Bot API oficial apenas.** Nunca userbot (MTProto/Telethon/Pyrogram) — `PeerFloodError`
   é o mesmo padrão de ban que nos trouxe aqui.

### Fora do escopo do v1 (YAGNI — anotado como futuro)

Arquivo pesquisável no Mini App; monetização (Telegram Stars / canal pago); grupo de
discussão bidirecional; dashboards de analytics. A base v1 deixa esses caminhos abertos.

## 2. O que já existe e será reaproveitado

- **`webhook/bot/users.py`** — store de usuários no Redis (roles admin/subscriber, status
  pending/approved, `subscriptions` por workflow), `get_subscribers_for_workflow`,
  `create_pending_user`, `approve_user`. Reusado para aprovar join requests.
- **`webhook/bot/routers/onboarding.py`** — `/start`, criação de pendente, cards de
  aprovação do admin, wizard de boas-vindas. Reusado para o fluxo de convite ao canal.
- **`webhook/bot/delivery.py`** — `deliver_to_subscribers(workflow_type, message)` (DM,
  text-only, sem throttle). Permanece para workflows internos; **não** vira o caminho do
  canal.
- **`execution/core/event_bus.py`** — `_EventsChannelSink` já **posta em um canal do
  Telegram** via `TELEGRAM_EVENTS_CHANNEL_ID`. **Padrão de referência** para o novo
  `channel_delivery`.
- **`webhook/routes/api.py`** (`store-draft`, linha ~100) — gancho `direct_delivery +
  workflow_type` que hoje chama `deliver_to_subscribers`. É o ponto de roteamento a estender.
- **`webhook/dispatch_document.py`** — fluxo de PDF (busca do OneDrive/SharePoint Graph
  `downloadUrl`), hoje envia via uazapi. Ganha um caminho de envio ao canal.

## 3. Arquitetura-alvo (v1)

### 3.1 Canal do cliente
Canal privado novo, **bot como admin**, id em `TELEGRAM_CLIENT_CHANNEL_ID` (env). Cada
relatório de cliente vira **um post**: resumo formatado (HTML/MarkdownV2) + **PDF anexo**
(quando houver) + **pin** do mais recente (opcional/configurável). Um post alcança todos os
assinantes — sem loop por usuário, sem pressão de rate limit.

### 3.2 Módulo `webhook/bot/channel_delivery.py` (novo)
Espelhado no `_EventsChannelSink`. Função pura/testável:

- `post_report_to_channel(message, pdf=None, *, silent=False, pin=False) -> dict`
  - formatação **HTML** (`parse_mode="HTML"`) — decisão travada: mais tolerante que
    MarkdownV2 (só escapa `< > &`), evita quebra por caractere especial no corpo do LLM
  - `send_message` do resumo; se `pdf`, `send_document` com legenda
  - **retry de flood-wait**: captura `TelegramRetryAfter`/HTTP 429, lê `retry_after`,
    dorme e retoma (a 74 assinantes num canal isso quase nunca dispara, mas o handler
    protege broadcasts internos e crescimento futuro)
  - `disable_notification=silent`; `pin` via `pin_chat_message` quando pedido
  - retorna `{"ok": bool, "message_id": int|None, "error": str|None}`; nunca levanta

### 3.3 Roteamento por workflow
Mapa único `WORKFLOW_DESTINATIONS` (ex: em `bot/config.py` ou módulo dedicado):
`daily_report|market_news|platts_reports → "client_channel"`;
`morning_check|baltic_ingestion → "internal"` (comportamento atual). O `store-draft`
consulta o mapa: destino `client_channel` → `post_report_to_channel`; `internal` →
caminho atual. **Não altera** o fluxo de curadoria/aprovação; só o passo de entrega final.

### 3.4 PDF no Telegram
Reusa a obtenção do PDF do `dispatch_document.py` (OneDrive Graph `downloadUrl`). O PDF é
enviado ao canal como documento com legenda (≤50 MB, folgado). No v1 o envio de PDF ao
canal **substitui** o envio uazapi para os workflows de cliente.

### 3.5 Onboarding / migração
Comando de admin no bot (ex: `/convite`) que:
- gera **invite link** do canal com `creates_join_request=True`, `expire_date` e
  `member_limit` (via `bot.create_chat_invite_link`)
- gera **QR** do link (lib de QR já disponível ou adicionar leve dependência)
- join requests chegam como card de aprovação ao admin (reusa `approve_user`/padrão de
  aprovação); ao aprovar, `bot.approve_chat_join_request`
Divulgação do link/QR por e-mail/ligação. Tracking simples de quem entrou no Redis (futuro).

### 3.6 Aposentadoria do uazapi (cliente)
Flag `CLIENT_DELIVERY_CHANNEL=telegram|uazapi` (default `telegram`). Com `telegram`, os
workflows de cliente não disparam mais o broadcast uazapi. Código uazapi permanece para
rollback, mas inativo no caminho de cliente.

## 4. Fluxo de dados (relatório de cliente, pós-migração)

```
workflow (GH Actions / curadoria) → store-draft (direct_delivery + workflow_type)
   → roteamento: workflow de cliente → client_channel
      → post_report_to_channel(resumo, pdf)
         → send_message (HTML) + send_document (PDF) [+ pin]
         → assinantes do canal recebem 1 post; view count = analytics de leitura
```

## 5. Tratamento de erros

- `channel_delivery` nunca levanta; retorna dict de status e loga.
- Flood-wait (429): respeita `retry_after`, no máximo N tentativas; depois marca falha.
- Canal ausente/bot sem permissão: loga erro claro ("bot precisa ser admin do canal") e
  reporta ao admin via chat interno.
- PDF indisponível (Graph falhou): posta o resumo mesmo assim, com aviso; não bloqueia.

## 6. Testes

- Unit `channel_delivery`: escape MarkdownV2/HTML; retry de flood-wait (mock
  429→`retry_after`→sucesso); envio com e sem PDF; `silent`/`pin`; retorno de status em
  falha (bot não-admin).
- Unit roteamento: mapa workflow→destino resolve client vs internal corretamente.
- Unit onboarding: geração de invite link com expiry/member_limit/join_request; aprovação
  chama `approve_chat_join_request`.

## 7. Rollout

1. Criar canal privado, adicionar o bot como admin, setar `TELEGRAM_CLIENT_CHANNEL_ID`.
2. Deploy com `CLIENT_DELIVERY_CHANNEL=telegram`.
3. Testar com 1-2 relatórios reais (admin observa view count).
4. Gerar convite + QR; migrar clientes por e-mail/ligação, aprovando join requests.
5. Após massa migrada, confirmar uazapi de cliente inativo.
