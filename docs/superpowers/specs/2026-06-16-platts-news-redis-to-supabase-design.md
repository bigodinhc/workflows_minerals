# Design: Migração das notícias Platts — Redis → Supabase (`platts_news`)

- **Data:** 2026-06-16
- **Status:** Aprovado (design); aguardando coordenadas do projeto Supabase de destino para implementação
- **Autor:** brainstorming colaborativo (usuário + Claude)

## 1. Problema & objetivo

Hoje as notícias do Platts vivem **só no Redis** (Railway), em JSON-por-chave, sem índice
de busca. Há dois estados: `platts:staging:<id>` (TTL 48h, fila de curadoria) e
`platts:archive:<date>:<id>` (sem TTL, registro permanente). A durabilidade depende de
RDB/`noeviction` do Redis e não há banco relacional para as notícias.

O objetivo é tornar as notícias **permanentes em Supabase** (já pago premium), com camada
de **retrieval (full-text)** embutida, **sem quebrar a ingestão** — notícia tem que
continuar chegando. A preocupação central é frescura/ingestão, não "storage vs storage".

### Decisões travadas (do brainstorming)

1. **Escopo:** persistir **toda notícia scrapeada** no Supabase (não só as curadas), com
   coluna `status`. Nada se perde por expirar na fila ou por rejeição.
2. **Dedup:** idempotência por `id` (hash do título) — `ON CONFLICT (id) DO NOTHING`.
3. **Fonte da verdade:** Supabase para leitura/registro. Redis permanece como **memória de
   trabalho do scraper** (fila `staging` 48h + ledger de dedup `seen`/`scraped`). O
   consumidor/copilot nunca depende do Redis como storage.
4. **Tabela dedicada** `platts_news` (não uma `documents` genérica).
5. **Tabela mora no projeto `liqiwvueesohlnnmezyw` (antigravity-reports)** — o projeto que
   ESTE repo já gerencia (migration owner único → sem ledger drift) e onde o scraper já
   escreve (`event_log`, `contacts`). **Descartado o `ironmkt_mvp` (tcpdfokvzsrqrevpqozw,
   trading prod)** para não fazer escrita cross-project no trading nem disputar o ledger de
   migration (cf. IRO-134). `NewsRepo` reutiliza as creds existentes
   (`SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY`); `NEWS_SUPABASE_*` é override opcional para
   migrar a um banco pristine no futuro sem reescrever código.
6. **Retrieval dentro da tabela:** coluna `tsvector` `GENERATED ALWAYS … STORED` + índice
   GIN, incluída no próprio `CREATE TABLE`. Sem serviço de busca à parte, sem pipeline de
   sync. Leitura direta. **Full-text no v1; embeddings (`pgvector`) deferidos.**
7. **Sem `platts:archive:*`:** ninguém externo lê essa chave (confirmado), então paramos de
   escrevê-la. Supabase assume o papel de archive permanente.

## 2. Papéis pós-migração

| Camada | Guarda | Muda? |
|---|---|---|
| **Redis** (buffer do scraper) | `staging` 48h (alimenta `/queue`) + ledger `seen`/`scraped` | Dedup/fila **intocados** (só +1 escrita no Supabase ao lado) |
| **Supabase `platts_news`** (projeto separado) | **toda** notícia, permanente, dirigida por `status`, com full-text | Nova — fonte da verdade de leitura |
| **Redis `platts:archive:*`** | — | **Deixa de ser escrito** |

Separação: **fila viva = Redis** (working view, 48h) · **registro/histórico/busca = Supabase**.

## 3. Schema `platts_news`

```sql
create table platts_news (
  id            text primary key,             -- sha256(título normalizado)[:12] = chave de dedup
  type          text not null default 'news', -- 'news' | 'rationale'
  status        text not null default 'staged'
                check (status in ('staged','archived','rejected','expired')),
  title         text not null,
  href          text,
  source        text,
  author        text,
  publish_date        text,   -- string crua como scrapeada (cuidado MM/DD — preserva o raw)
  publish_date_parsed date,   -- best-effort, nullable
  full_text     text,
  paragraphs    jsonb,
  tables        jsonb,
  metadata      jsonb,
  raw           jsonb,        -- objeto original inteiro (rede de segurança / forward-compat)
  scraped_at    timestamptz not null default now(),  -- era stagedAt
  archived_at   timestamptz,
  archived_by   bigint,       -- telegram chat_id
  rejected_at   timestamptz,
  reject_reason text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  fts tsvector generated always as (
    to_tsvector('english', coalesce(title,'') || ' ' || coalesce(full_text,''))
  ) stored
);

create index platts_news_status_scraped_idx on platts_news (status, scraped_at desc);
create index platts_news_archived_idx       on platts_news (archived_at desc) where status = 'archived';
create index platts_news_fts_idx            on platts_news using gin (fts);

-- RLS: ligado, com policy service-role (ajustar à convenção do projeto de destino).
-- Deferido v2: coluna embedding vector(1536) + índice ivfflat/hnsw p/ busca semântica.
```

Notas:
- Os ~20 campos do JSON atual mapeiam direto; campos ricos (`paragraphs`/`tables`/`metadata`)
  viram JSONB, e `raw` guarda o objeto original inteiro.
- `'english'` no tsvector porque a notícia do Platts é em inglês (trocável por `'simple'`).
- A coluna gerada se auto-mantém em insert/update — sem trigger, sem sync.

## 4. Acesso ao projeto separado

- Client `NewsRepo` resolve credenciais com fallback:
  - `NEWS_SUPABASE_URL`/`NEWS_SUPABASE_SERVICE_KEY` **se setadas** (override → banco pristine futuro),
  - **senão** `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` (creds que o repo já usa para `liqiwvueesohlnnmezyw`).
- Hoje: **nenhum secret novo** — backfill, ingestão e CI já têm `SUPABASE_*`. `NEWS_*` documentado como opcional.
- `NewsRepo` expõe a **mesma interface** das funções de curadoria de hoje para minimizar o
  blast radius: `upsert_scraped`, `archive`, `reject`, `list_archive_recent`, `get_by_id`,
  `search`.

## 5. Fluxo de escrita

### Ingestão — `execution/curation/router.py` (linhas ~36-38)
Adiciona **uma** chamada ao lado das três existentes (Redis intocado):
```python
redis_client.set_staging(item_id, to_stage)    # mantém — fila 48h
redis_client.mark_seen(item_id)                # mantém — dedup ledger
redis_client.mark_scraped(today_date, item_id) # mantém — telemetria
news_repo.upsert_scraped(item_id, item)        # NOVO — INSERT ... ON CONFLICT (id) DO NOTHING, status='staged'
```
Dedup duplo de propósito: Redis `seen` (rápido, 30d) decide se entra na fila; `ON CONFLICT
(id)` no Postgres impede linha duplicada **para sempre**, mesmo após a janela de 30d.

### Curadoria — `webhook/bot/routers/callbacks_curation.py` e `callbacks_queue.py`
Ordem importa (evita drift): **Supabase primeiro (durável), Redis depois.**
1. `UPDATE platts_news SET status='archived', archived_at=now(), archived_by=$chat WHERE id=$id`
   (reject: `status='rejected', rejected_at=now(), reject_reason=$r`).
2. Só se ok → Redis `delete staging` (como hoje).
3. Se o Supabase falhar → aborta, item permanece na fila para retry.
4. **Para de escrever `platts:archive:*`.**

## 6. Fluxo de leitura (re-point para Supabase)

| Ponto hoje | Vira |
|---|---|
| `/queue` (`list_staging`) | **continua Redis** (fila viva 48h) |
| `/history` (`list_archive_recent`) | `SELECT … WHERE status='archived' ORDER BY archived_at DESC LIMIT n` |
| reprocess (`get_archive(date,id)`) | `SELECT … WHERE id=$id` (some o date-na-chave) |
| preview (`webhook/routes/preview.py`) | `SELECT … WHERE id=$id` |
| mini-app (`webhook/routes/mini_api.py`) | `SELECT` equivalentes |
| **busca (novo)** | `SELECT … WHERE fts @@ websearch_to_tsquery('english', $q) ORDER BY ts_rank(...) DESC` |

Bônus: acaba o `get_archive(today/yesterday)` e o `SCAN` — viram queries indexadas.

## 7. Carga única (os 243 itens existentes)

Script one-shot (`execution/scripts/migrate_archive_to_supabase.py`):
`SCAN platts:archive:*` → parse JSON → `INSERT` em `platts_news` com `status='archived'`,
mapeando `archivedAt → archived_at`, `archivedBy → archived_by`, `stagedAt → scraped_at`.
Idempotente (`ON CONFLICT DO NOTHING`); pode rodar N vezes. Volume ~2,7MB / 243 linhas.

## 8. Comportamento `expired`

Notícia que sai da fila (staging expira em 48h sem curadoria) **permanece `status='staged'`
no Supabase** — estado verdadeiro (scrapeada, nunca acionada). Um cron opcional pode marcar
`staged` com `scraped_at > 2 dias` como `expired`, apenas para higiene de relatório.
**Incluído como opcional, default desligado.**

## 9. Testes

- Fakes de Redis existentes ganham um fake de `NewsRepo` (mock do client Supabase).
- Cobrir: ingestão (upsert + dedup ON CONFLICT), curadoria (transição de status + ordem
  Supabase→Redis + abort em falha), leituras re-apontadas, busca full-text, e o script de
  carga única (idempotência).
- Meta: manter o nível de cobertura atual do módulo de curadoria.

## 10. Unidades / arquivos

| Unidade | Responsabilidade | Depende de |
|---|---|---|
| `execution/integrations/news_supabase_client.py` | Conexão ao projeto Supabase de notícias | env `NEWS_SUPABASE_*` |
| `execution/curation/news_repo.py` | CRUD + busca de `platts_news` (mesma interface da curadoria) | client acima |
| `execution/curation/router.py` | + 1 chamada `upsert_scraped` na ingestão | `news_repo` |
| `webhook/bot/routers/callbacks_curation.py` / `callbacks_queue.py` | curadoria escreve status no Supabase | `news_repo` |
| `webhook/redis_queries.py` (leituras de archive) | re-apontar para `news_repo` | `news_repo` |
| `execution/scripts/migrate_archive_to_supabase.py` | carga única dos 243 itens | redis + `news_repo` |
| `supabase/migrations/<ts>_platts_news.sql` | schema + índices + tsvector + RLS (versionado aqui) | projeto de destino conectado |

## 11. Dependências externas / pendências

- **Conexão do projeto Supabase de destino a esta instância** + env vars `NEWS_SUPABASE_URL`
  / `NEWS_SUPABASE_SERVICE_KEY`. A migração SQL e o teste end-to-end rodam contra esse projeto.
- **Migração `CREATE TABLE platts_news` versionada aqui** (`supabase/migrations/`) e aplicada
  por nós nesta instância (não pela outra instância).
- Idioma do tsvector confirmado: `'english'`.

## 12. Fora de escopo (v1)

- Embeddings / busca semântica (`pgvector`) — ponto de extensão deferido.
- Remoção total do Redis (drafts, idempotência, estado de UI permanecem no Redis).
- Migração de `webhook:feedback:*` para Supabase (pode virar coluna/tabela depois).
