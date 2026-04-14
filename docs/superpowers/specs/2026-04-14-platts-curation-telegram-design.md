# Platts News Curation via Telegram — Design

**Date:** 2026-04-14
**Status:** Approved by user, pending implementation plan

## Problem

Hoje o projeto tem dois scripts de ingestion (`rationale_ingestion.py`, `market_news_ingestion.py`) que rodam o mesmo Apify actor (`platts-scrap-full-news`) com configs diferentes e redundantes. Ambos mandam **um draft agregado** pro Telegram, sem permitir curadoria item-a-item. O admin (user) quer:

1. Curar artigos individualmente antes de entrar no pipeline de AI
2. Ter 3 ações por item: arquivar (pra consumo em outro projeto via Redis), recusar, ou mandar pro pipeline dos 3 agents (Writer → Critique → Curator) que já existe em `webhook/app.py`
3. Receber preview estruturado no Telegram com link pra ver conteúdo completo em HTML
4. Manter fluxo automático do rationale (preços) sem curadoria — roda direto pro `RationaleAgent`

## Scope

### In scope
- Unificar `rationale_ingestion.py` + `market_news_ingestion.py` num único script `execution/scripts/platts_ingestion.py`
- Nova camada de curadoria (`execution/curation/`): grava Redis staging, posta Telegram com preview + 4 botões
- Nova rota HTML `/preview/<id>` no webhook Flask pra exibir conteúdo completo dentro do browser do Telegram
- 3 novos callbacks no `webhook/app.py:handle_callback()`: `curate_archive`, `curate_reject`, `curate_pipeline`
- Dedup via Redis SET
- Arquivo permanente em Redis pra consumo externo

### Out of scope
- Mudanças no `RationaleAgent` ou no `run_3_agents()` (Writer/Critique/Curator) — ambos ficam intocados
- Dashboard Next.js (fica intocado)
- Mudanças em `baltic_ingestion.py`, `send_daily_report.py`, `morning_check.py`, `send_news.py`
- Autenticação no preview HTML (URL opaca com ID sha256 é considerada segurança suficiente pro beta)
- Persistência histórica além de Redis (sem Postgres/Supabase nesse escopo)

## Architecture

### High-level flow

```
[Cron 9h/12h/15h BRT]
      ↓
[execution/scripts/platts_ingestion.py]
      ↓
[Apify: platts-scrap-full-news] — sources: allInsights + ironOreTopic + rmw
      ↓
[execution/curation/router.py] — para cada artigo do dataset:
      │
      ├─ Classifica: rationale? (source=rmw AND tabName matches "Rationale|Lump")
      │
      ├─ Rationale path (gated por flag platts:rationale:processed:{date}):
      │     ↓
      │     Se flag não setada: agrega TODOS os rationale items do run
      │     → RationaleAgent.process() → draft → Telegram [Aprovar/Ajustar/Rejeitar]
      │     → WhatsApp (AUTO) → SET NX flag com TTL 30h
      │     Se flag setada: pula (rationale já saiu hoje)
      │     Nota: rationale NÃO passa pelo seen:<date> — gating é só via flag diária
      │
      └─ Curation path (allInsights, ironOreTopic, RMW BOTs/Summary):
            ↓
            SISMEMBER platts:seen:{date} {id}? → pula se já visto
            SET platts:staging:{id} (JSON, TTL 48h)
            SADD platts:seen:{date} {id} (TTL 30d)
            Posta Telegram (chat_id = TELEGRAM_CHAT_ID env): preview + 4 botões
```

### Callbacks (novos em `webhook/app.py:handle_callback`)

| Callback | Ação |
|---|---|
| `curate_archive:<id>` | GET staging → adiciona archivedAt/archivedBy → SET `platts:archive:<date>:<id>` (sem TTL) → DEL staging → edita msg |
| `curate_reject:<id>` | DEL staging → edita msg (não arquiva; seen:<date> previne reposting no mesmo dia) |
| `curate_pipeline:<id>` | GET staging → monta raw_text → chama `run_3_agents()` (background thread, já existe) → arquiva ao fim → edita msg |

### Preview HTML route (`/preview/<id>`)

- Rota Flask nova em `webhook/app.py`
- Lê `platts:staging:<id>` OU `platts:archive:*:<id>` (fallback pra itens já arquivados)
- Renderiza Jinja template com Tailwind CDN
- Layout: título H1, meta (source/date/author/tabName), fullText em parágrafos, tabelas (se existirem), link pro article original no Platts
- URL pública via Railway webhook host (já deployed)

## Data model

### Redis keyspaces

**`platts:staging:<id>`** — item pending admin decision
- Type: String (JSON)
- TTL: 48h
- Campos JSON:
  ```json
  {
    "id": "a3f9c2d1e5b8",
    "title": "China steel output lags 2025...",
    "fullText": "<texto completo, sem truncar>",
    "publishDate": "04/09/2026 13:46:04 UTC",
    "source": "Top News - Ferrous Metals",
    "tabName": "",
    "author": "Jing Zhang",
    "url": "https://core.spglobal.com/#platts/insightsArticle?...",
    "extractedAt": "2026-04-14T07:26:57Z",
    "tables": [...]
  }
  ```

**`platts:archive:<YYYY-MM-DD>:<id>`** — item arquivado pelo admin (consumível por outro projeto)
- Type: String (JSON)
- TTL: none (permanente)
- Mesmo schema do staging + `archivedAt`, `archivedBy` (chat_id do Telegram)

**`platts:seen:<YYYY-MM-DD>`** — dedup set diário
- Type: Set
- TTL: 30 dias
- Members: `<id>` strings

**`platts:rationale:processed:<YYYY-MM-DD>`** — flag de rationale já processado no dia
- Type: String (empty / "1")
- TTL: 30 horas
- Setado via `SET NX` após sucesso do `RationaleAgent.process()` — garante 1x por dia

### ID generation

```python
id = sha256(f"{source}::{title}").hexdigest()[:12]
```

Determinístico cross-run: mesmo artigo = mesmo ID, permite dedup e match com staging/archive keys.

### Telegram message format

```
🔴 FLASH 02/20/2026 15:09 UTC
━━━━━━━━━━━━━━━━━━━━

Supreme Court strikes down Trump's global tariffs

<preview 400 chars do fullText, ... se cortou>

━━━━━━━━━━━━━━━━━━━━
📰 Top News - Ferrous Metals
✍️ Jing Zhang
📅 04/09/2026 13:46 UTC
🆔 a3f9c2d1

[📖 Ler completo]   ← URL button → /preview/<id>
[✅ Arquivar]  [❌ Recusar]  [🤖 3 Agents]
```

Header varia por tipo:
- **FLASH**: `🔴 FLASH {date}`
- **Outros**: `📰 {source}` no footer

## Components

### `execution/scripts/platts_ingestion.py` (novo)
- Entry point. Argparse (`--dry-run`, `--target-date`).
- Chama Apify actor, faz dedup, roteia itens.
- Substitui `rationale_ingestion.py` + `market_news_ingestion.py`.

### `execution/curation/` (módulo novo)
- **`redis_client.py`** — wrapper fino em cima do `REDIS_URL` existente, métodos: `set_staging`, `get_staging`, `archive`, `is_seen`, `mark_seen`, `set_rationale_flag`
- **`id_gen.py`** — `generate_id(source, title) -> str`
- **`telegram_poster.py`** — monta mensagem Markdown + inline keyboard, envia via `TelegramClient`
- **`router.py`** — recebe dataset items → classifica (rationale vs outros) → dispara: `RationaleAgent` flow OU `telegram_poster.post_for_curation()`

### `webhook/app.py` (modificado)
- Nova rota `/preview/<id>` — serve HTML via Jinja template
- Novos handlers em `handle_callback()`: `curate_archive`, `curate_reject`, `curate_pipeline`
- Template Jinja `templates/preview.html` (Tailwind CDN)

### Removidos
- `execution/agents/market_news_agent.py` (MarketNewsAgent — substituído por run_3_agents)
- `execution/scripts/market_news_ingestion.py` (funcionalidade absorvida)
- `execution/scripts/rationale_ingestion.py` (funcionalidade absorvida)

### Intocados
- `execution/agents/rationale_agent.py` (RationaleAgent — orquestrador interno 3-phase)
- `webhook/app.py:run_3_agents()` (Writer/Critique/Curator chain)
- `baltic_ingestion.py`, `send_daily_report.py`, `morning_check.py`, `send_news.py`
- Dashboard Next.js

## Error handling

**Redis offline:**
- Ingestion: aborta com erro crítico, `state_store.record_crash()`. Não posta items sem staging (evitaria botões quebrados).
- Callbacks: retornam mensagem "⚠️ Redis indisponível, tenta de novo" no Telegram. Webhook não crasha.

**Apify offline/falha:**
- Ingestion: `state_store.record_crash()`. 15h run serve de retry do 12h.

**Item expirou no staging (TTL 48h):**
- Callback edita msg "⚠️ Item expirou ou já processado". Não quebra.

**Callback duplicado (user clicou 2x):**
- Primeiro wins. Segundo acha staging vazio → "⚠️ Já processado".

**Rationale falha no meio do pipeline:**
- Flag `platts:rationale:processed:<date>` só é setada **após sucesso completo**. Próxima run tenta de novo.

**Erro no `run_3_agents()`:**
- Fluxo atual já trata (mensagem de erro no Telegram). Mantido.

## Schedule

Cron no Railway (UTC):
- `0 12,15,18 * * *` UTC = 9h, 12h, 15h BRT
- Script único: `python -m execution.scripts.platts_ingestion`

Rationale: processado dentro do run quando detectado novo item rationale + flag do dia ainda não setada. Platts publica rationale ~12 UTC, então 9h BRT (12 UTC) é primeira janela viável; 12h/15h BRT = retry natural.

## Observability

- `WorkflowLogger("PlattsIngestion")` — log estruturado
- `state_store.record_success/empty/crash` por run
- Métricas no summary: `total_collected`, `new_after_dedup`, `rationale_processed`, `curation_posted`, `failed_articles`

## Testing strategy

- **Unit:** `id_gen` determinismo, Redis staging/archive round-trip, dedup logic, message formatter
- **Integration:** end-to-end com Redis local + Apify real (`--dry-run` no Telegram, envia pra chat de teste)
- **Manual:** 1 run real full, verifica:
  - Cada tipo de item (FLASH, Top News, Latest, News & Insights, RMW BOTs) chega formatado certo
  - Rationale dispara AUTO, 1 msg só com draft AI
  - Cada botão (`Arquivar`, `Recusar`, `3 Agents`) executa ação esperada + edita msg
  - `/preview/<id>` carrega full content + tabelas renderizadas
  - Dedup impede repostar no 12h se 9h já pegou

## Migration

1. Implementar novos componentes sem quebrar os scripts antigos
2. Testar `platts_ingestion.py` em paralelo (cron desativado)
3. Validar 1 dia completo (3 runs)
4. Desativar crons dos scripts antigos
5. `git rm` dos 3 arquivos removidos (market_news_ingestion, rationale_ingestion, market_news_agent)
6. Arquivar actor Apify antigo `platts-news-only` no Console (user manual)

## Open questions

Nenhuma — todas resolvidas durante brainstorming.
