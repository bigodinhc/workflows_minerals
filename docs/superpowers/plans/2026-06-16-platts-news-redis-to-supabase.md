# Migração das notícias Platts (Redis → Supabase) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persistir toda notícia scrapeada do Platts numa tabela dedicada `platts_news` (projeto Supabase separado), com full-text search embutido, mantendo o Redis como buffer de trabalho do scraper (fila 48h + dedup) e a ingestão intacta.

**Architecture:** Um `NewsRepo` (Supabase, projeto próprio via `NEWS_SUPABASE_*`) espelha a interface de curadoria existente. A ingestão (`router._stage_only`) ganha **um** upsert idempotente (`ON CONFLICT (id) DO NOTHING`, `status='staged'`) ao lado das escritas Redis. A curadoria (Telegram) passa a gravar `status` no Supabase e descartar a chave de staging do Redis (para de escrever `platts:archive:*`). As leituras de archive (`/history`, reprocess, preview, mini-app) re-apontam para o Supabase. Carga única migra os 243 itens de `platts:archive:*`.

**Tech Stack:** Python 3.10, `supabase>=2.0.0` (PostgREST v2), `redis`/`fakeredis`, `pytest`, aiogram. Postgres com `tsvector` gerado + índice GIN. Dependências instaladas via `uv pip` (pip do sistema quebrado nesta máquina).

---

## File Structure

| Arquivo | Responsabilidade | Ação |
|---|---|---|
| `supabase/migrations/20260616_platts_news.sql` | DDL: tabela + índices + tsvector + trigger + RLS | Criar |
| `execution/integrations/news_supabase_client.py` | Conexão cacheada ao projeto Supabase de notícias (`NEWS_SUPABASE_*`) | Criar |
| `execution/curation/news_repo.py` | CRUD + busca de `platts_news` (mapeamento item→row, upsert, status, get, list, search) | Criar |
| `execution/curation/router.py` | + 1 chamada best-effort `news_repo.upsert_scraped` em `_stage_only` | Modificar |
| `webhook/bot/routers/callbacks_curation.py` | archive/reject/send_raw gravam status no Supabase + discard no Redis | Modificar |
| `webhook/bot/routers/callbacks_queue.py` | bulk archive/discard gravam status no Supabase + discard no Redis | Modificar |
| `webhook/redis_queries.py` | `list_archive_recent` e `stats_for_date` (archived) delegam ao `news_repo` | Modificar |
| `webhook/bot/routers/_helpers.py` | reprocess lê archive via `news_repo.get_by_id` | Modificar |
| `webhook/routes/preview.py` | preview lê archive via `news_repo.get_by_id` | Modificar |
| `webhook/routes/mini_api.py` | mini-app lê archive/list via `news_repo` | Modificar |
| `execution/scripts/migrate_archive_to_supabase.py` | carga única dos 243 itens `platts:archive:*` | Criar |
| `tests/test_news_repo.py` | testes do `NewsRepo` (mock do client supabase) | Criar |
| `tests/test_router_supabase_upsert.py` | testes da ingestão re-apontada | Criar |
| `tests/test_migrate_archive_to_supabase.py` | testes do script de carga única | Criar |
| `.env.example`, `.github/workflows/market_news.yml` | env vars `NEWS_SUPABASE_*` | Modificar |

**Ordem das fases:** 0 (schema) → 1 (NewsRepo) → 2 (ingestão) → 3 (curadoria) → 4 (leituras) → 5 (carga única) → 6 (env/CI). Cada fase deixa o repo verde.

---

## Phase 0 — Schema & migração

### Task 0.1: Criar a migração SQL `platts_news`

**Files:**
- Create: `supabase/migrations/20260616_platts_news.sql`

- [ ] **Step 1: Escrever o DDL** (espelha o estilo de `supabase/migrations/20260422_contacts.sql`)

```sql
-- Phase: notícias Platts migradas de Redis (platts:staging/archive) para Postgres.
-- Projeto: instância Supabase dedicada a notícias (NÃO o banco de trading liqiwvueesohlnnmezyw).
-- Consumers: execution/curation/router.py (ingestão), webhook/bot/routers/* (curadoria),
--            webhook/redis_queries.py, webhook/routes/{preview,mini_api}.py, copilot (busca full-text).

create table if not exists platts_news (
  id            text        primary key,            -- sha256(título normalizado)[:12] = dedup
  type          text        not null default 'news',
  status        text        not null default 'staged'
                  check (status in ('staged','archived','rejected','expired')),
  title         text        not null,
  href          text,
  source        text,
  author        text,
  publish_date         text,                          -- string crua (cuidado MM/DD — preserva o raw)
  publish_date_parsed  date,                          -- best-effort, nullable
  full_text     text,
  paragraphs    jsonb,
  tables        jsonb,
  metadata      jsonb,
  raw           jsonb,                                 -- objeto original inteiro (forward-compat)
  scraped_at    timestamptz not null default now(),
  archived_at   timestamptz,
  archived_by   bigint,
  rejected_at   timestamptz,
  reject_reason text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  fts tsvector generated always as (
    to_tsvector('english', coalesce(title,'') || ' ' || coalesce(full_text,''))
  ) stored
);

create index if not exists platts_news_status_scraped_idx on platts_news (status, scraped_at desc);
create index if not exists platts_news_archived_idx       on platts_news (archived_at desc) where status = 'archived';
create index if not exists platts_news_fts_idx            on platts_news using gin (fts);

create or replace function platts_news_set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists platts_news_updated_at on platts_news;
create trigger platts_news_updated_at
  before update on platts_news
  for each row execute function platts_news_set_updated_at();

alter table platts_news enable row level security;
-- No policies. Only service_role bypasses RLS; anon/public get zero access.

comment on table platts_news is
  'Notícias Platts (iron ore). status: staged=na fila/não curada, archived=curada, '
  'rejected=recusada, expired=saiu da fila sem curadoria. Substituiu platts:archive:* do Redis em 2026-06-16.';
comment on column platts_news.id is 'sha256(título normalizado)[:12] — mesma chave de dedup do Redis.';
comment on column platts_news.raw is 'Objeto JSON original do scraper, preservado por garantia.';
```

- [ ] **Step 2: Commit**

```bash
git add supabase/migrations/20260616_platts_news.sql
git commit -m "feat: migração SQL da tabela platts_news (notícias)"
```

### Task 0.2: Aplicar a migração no projeto de notícias

> **Bloqueado por:** coordenadas do projeto Supabase de notícias (project ref + service key) conectado a esta instância.

- [ ] **Step 1: Linkar a CLI ao projeto de notícias** (substituir `<NEWS_PROJECT_REF>` pelas coordenadas reais)

Run: `supabase link --project-ref <NEWS_PROJECT_REF>`
Expected: "Finished supabase link."

- [ ] **Step 2: Aplicar a migração**

Run: `supabase db push`
Expected: aplica `20260616_platts_news.sql` sem erro.

Alternativa (se preferir não usar a CLI): colar o conteúdo do `.sql` no SQL Editor do projeto de notícias e executar.

- [ ] **Step 3: Verificar a tabela**

Run (SQL Editor ou `psql`): `select count(*) from platts_news;`
Expected: `0`.

---

## Phase 1 — NewsRepo

### Task 1.1: Client Supabase dedicado a notícias

**Files:**
- Create: `execution/integrations/news_supabase_client.py`

- [ ] **Step 1: Escrever o client cacheado** (mesmo padrão de `_get_client` do redis_client)

```python
"""Client Supabase dedicado ao projeto de NOTÍCIAS (separado do banco de trading).

Usa NEWS_SUPABASE_URL / NEWS_SUPABASE_SERVICE_KEY. Mantido separado de
execution/integrations/supabase_client.py (que aponta para o projeto de trading)
para não misturar credenciais nem domínios.
"""
from __future__ import annotations

import os

_client = None


def get_news_client():
    """Return a cached supabase Client for the news project.

    Raises RuntimeError if env vars are unset, so a misconfig fails loud
    instead of silently writing to the wrong project.
    """
    global _client
    if _client is not None:
        return _client
    from supabase import create_client
    url = os.getenv("NEWS_SUPABASE_URL", "").strip()
    key = os.getenv("NEWS_SUPABASE_SERVICE_KEY", "").strip()
    if not url or not key:
        raise RuntimeError("NEWS_SUPABASE_URL / NEWS_SUPABASE_SERVICE_KEY not set")
    _client = create_client(url, key)
    return _client
```

- [ ] **Step 2: Commit**

```bash
git add execution/integrations/news_supabase_client.py
git commit -m "feat: client supabase dedicado ao projeto de notícias"
```

### Task 1.2: NewsRepo — mapeamento item→row + upsert idempotente

**Files:**
- Create: `execution/curation/news_repo.py`
- Test: `tests/test_news_repo.py`

- [ ] **Step 1: Escrever os testes que falham**

```python
"""Tests for execution.curation.news_repo (mock do client supabase)."""
from unittest.mock import MagicMock
import pytest


@pytest.fixture
def fake_sb(monkeypatch):
    """Inject a MagicMock supabase client and reset the cached one."""
    client = MagicMock(name="supabase_client")
    from execution.curation import news_repo
    monkeypatch.setattr(news_repo, "get_news_client", lambda: client)
    return client


def _last_table(client):
    """Return the table name passed to the most recent .table(...) call."""
    return client.table.call_args[0][0]


def test_item_to_row_maps_fields():
    from execution.curation.news_repo import _item_to_row
    item = {
        "title": "Brazil ore climbs", "href": "http://x", "source": "Top News",
        "author": "Reuters", "publishDate": "06/15/2026", "fullText": "body",
        "paragraphs": ["a", "b"], "tables": [{"h": 1}], "metadata": {"w": 2},
        "type": "news", "stagedAt": "2026-06-15T10:00:00+00:00",
    }
    row = _item_to_row("abc123", item, status="staged")
    assert row["id"] == "abc123"
    assert row["status"] == "staged"
    assert row["title"] == "Brazil ore climbs"
    assert row["full_text"] == "body"
    assert row["publish_date"] == "06/15/2026"
    assert row["paragraphs"] == ["a", "b"]
    assert row["raw"] == item
    assert row["scraped_at"] == "2026-06-15T10:00:00+00:00"


def test_item_to_row_omits_none_scraped_at():
    """No stagedAt → scraped_at absent so the DB default now() applies."""
    from execution.curation.news_repo import _item_to_row
    row = _item_to_row("abc", {"title": "T"}, status="staged")
    assert "scraped_at" not in row


def test_upsert_scraped_calls_upsert_with_on_conflict(fake_sb):
    from execution.curation.news_repo import upsert_scraped
    fake_sb.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[{"id": "abc"}])
    inserted = upsert_scraped("abc", {"title": "T", "fullText": "x"})
    assert _last_table(fake_sb) == "platts_news"
    kwargs = fake_sb.table.return_value.upsert.call_args.kwargs
    assert kwargs.get("on_conflict") == "id"
    assert kwargs.get("ignore_duplicates") is True
    assert inserted is True


def test_upsert_scraped_returns_false_on_conflict(fake_sb):
    from execution.curation.news_repo import upsert_scraped
    fake_sb.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])
    assert upsert_scraped("dup", {"title": "T"}) is False
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `source .venv/bin/activate && pytest tests/test_news_repo.py -v`
Expected: FAIL com `ModuleNotFoundError: execution.curation.news_repo`.

- [ ] **Step 3: Implementar `news_repo.py` (mapa + upsert)**

```python
"""Repositório Supabase da tabela platts_news.

Espelha a interface de curadoria do Redis (set_staging/archive/get_archive/...)
para minimizar o blast radius nos call sites. Toda escrita usa o client do
projeto de notícias (news_supabase_client.get_news_client).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from execution.integrations.news_supabase_client import get_news_client

TABLE = "platts_news"


def _item_to_row(item_id: str, item: dict, status: str = "staged") -> dict:
    """Map a scraper item dict to a platts_news row. Omits keys that should
    fall back to DB defaults (scraped_at)."""
    row = {
        "id": item_id,
        "type": item.get("type") or "news",
        "status": status,
        "title": item.get("title") or "",
        "href": item.get("href") or item.get("url"),
        "source": item.get("source"),
        "author": item.get("author"),
        "publish_date": item.get("publishDate") or item.get("date"),
        "full_text": item.get("fullText"),
        "paragraphs": item.get("paragraphs"),
        "tables": item.get("tables"),
        "metadata": item.get("metadata"),
        "raw": item,
    }
    staged_at = item.get("stagedAt")
    if staged_at:
        row["scraped_at"] = staged_at
    return row


def upsert_scraped(item_id: str, item: dict) -> bool:
    """Idempotent insert at ingestion. ON CONFLICT (id) DO NOTHING.

    Returns True if a new row was inserted, False if it already existed.
    """
    row = _item_to_row(item_id, item, status="staged")
    resp = (
        get_news_client()
        .table(TABLE)
        .upsert(row, on_conflict="id", ignore_duplicates=True)
        .execute()
    )
    return bool(getattr(resp, "data", None))
```

- [ ] **Step 4: Rodar e ver passar**

Run: `pytest tests/test_news_repo.py -v`
Expected: PASS (4 testes).

- [ ] **Step 5: Commit**

```bash
git add execution/curation/news_repo.py tests/test_news_repo.py
git commit -m "feat: NewsRepo com upsert idempotente de notícias scrapeadas"
```

### Task 1.3: NewsRepo — status (archive/reject), get, list, search

**Files:**
- Modify: `execution/curation/news_repo.py`
- Test: `tests/test_news_repo.py`

- [ ] **Step 1: Adicionar os testes que falham**

```python
def test_set_status_archived_sets_fields(fake_sb):
    from execution.curation.news_repo import set_status
    upd = fake_sb.table.return_value.update.return_value
    upd.eq.return_value.execute.return_value = MagicMock(data=[{"id": "abc"}])
    ok = set_status("abc", "archived", chat_id=999)
    payload = fake_sb.table.return_value.update.call_args[0][0]
    assert payload["status"] == "archived"
    assert payload["archived_by"] == 999
    assert "archived_at" in payload
    assert ok is True


def test_set_status_rejected_sets_reason(fake_sb):
    from execution.curation.news_repo import set_status
    upd = fake_sb.table.return_value.update.return_value
    upd.eq.return_value.execute.return_value = MagicMock(data=[{"id": "abc"}])
    set_status("abc", "rejected", reason="fora de escopo")
    payload = fake_sb.table.return_value.update.call_args[0][0]
    assert payload["status"] == "rejected"
    assert payload["reject_reason"] == "fora de escopo"
    assert "rejected_at" in payload


def test_set_status_returns_false_when_no_row(fake_sb):
    from execution.curation.news_repo import set_status
    upd = fake_sb.table.return_value.update.return_value
    upd.eq.return_value.execute.return_value = MagicMock(data=[])
    assert set_status("missing", "archived", chat_id=1) is False


def test_set_status_bulk_uses_in_filter(fake_sb):
    from execution.curation.news_repo import set_status_bulk
    upd = fake_sb.table.return_value.update.return_value
    upd.in_.return_value.execute.return_value = MagicMock(data=[{"id": "a"}, {"id": "b"}])
    n = set_status_bulk(["a", "b"], "archived", chat_id=1)
    assert fake_sb.table.return_value.update.return_value.in_.call_args[0][0] == "id"
    assert n == 2


def test_get_by_id_returns_row(fake_sb):
    from execution.curation.news_repo import get_by_id
    chain = fake_sb.table.return_value.select.return_value.eq.return_value.limit.return_value
    chain.execute.return_value = MagicMock(data=[{"id": "abc", "title": "T"}])
    got = get_by_id("abc")
    assert got["title"] == "T"


def test_get_by_id_returns_none_when_empty(fake_sb):
    from execution.curation.news_repo import get_by_id
    chain = fake_sb.table.return_value.select.return_value.eq.return_value.limit.return_value
    chain.execute.return_value = MagicMock(data=[])
    assert get_by_id("missing") is None


def test_list_by_status_orders_and_limits(fake_sb):
    from execution.curation.news_repo import list_by_status
    chain = fake_sb.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value
    chain.execute.return_value = MagicMock(data=[{"id": "a"}])
    rows = list_by_status("archived", limit=10)
    assert rows == [{"id": "a"}]


def test_search_uses_text_search(fake_sb):
    from execution.curation.news_repo import search
    chain = fake_sb.table.return_value.select.return_value.text_search.return_value.limit.return_value
    chain.execute.return_value = MagicMock(data=[{"id": "a", "title": "iron ore"}])
    rows = search("iron ore", limit=5)
    args = fake_sb.table.return_value.select.return_value.text_search.call_args
    assert args[0][0] == "fts"
    assert args[0][1] == "iron ore"
    assert rows[0]["title"] == "iron ore"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pytest tests/test_news_repo.py -v`
Expected: FAIL com `ImportError: cannot import name 'set_status'` (e demais).

- [ ] **Step 3: Implementar as funções**

```python
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def set_status(item_id: str, status: str, *,
               chat_id: Optional[int] = None, reason: Optional[str] = None) -> bool:
    """Update a row's status. Returns True if a row was updated.

    archived → stamps archived_at/archived_by. rejected → stamps rejected_at/reject_reason.
    """
    payload: dict = {"status": status}
    if status == "archived":
        payload["archived_at"] = _now_iso()
        if chat_id is not None:
            payload["archived_by"] = chat_id
    elif status == "rejected":
        payload["rejected_at"] = _now_iso()
        if reason is not None:
            payload["reject_reason"] = reason
    resp = get_news_client().table(TABLE).update(payload).eq("id", item_id).execute()
    return bool(getattr(resp, "data", None))


def set_status_bulk(item_ids: list[str], status: str, *,
                    chat_id: Optional[int] = None) -> int:
    """Update many rows' status in one query. Returns count of rows updated."""
    if not item_ids:
        return 0
    payload: dict = {"status": status}
    if status == "archived":
        payload["archived_at"] = _now_iso()
        if chat_id is not None:
            payload["archived_by"] = chat_id
    elif status == "rejected":
        payload["rejected_at"] = _now_iso()
    resp = get_news_client().table(TABLE).update(payload).in_("id", item_ids).execute()
    return len(getattr(resp, "data", None) or [])


def get_by_id(item_id: str) -> Optional[dict]:
    """Read a single row by id. Returns None if missing."""
    resp = (
        get_news_client().table(TABLE).select("*").eq("id", item_id).limit(1).execute()
    )
    data = getattr(resp, "data", None) or []
    return data[0] if data else None


def list_by_status(status: str, limit: int = 10) -> list[dict]:
    """List rows of a given status, newest archived/scraped first."""
    order_col = "archived_at" if status == "archived" else "scraped_at"
    resp = (
        get_news_client().table(TABLE).select("*").eq("status", status)
        .order(order_col, desc=True).limit(limit).execute()
    )
    return getattr(resp, "data", None) or []


def search(query: str, limit: int = 10) -> list[dict]:
    """Full-text search over title+full_text via the generated tsvector column."""
    resp = (
        get_news_client().table(TABLE).select("*")
        .text_search("fts", query, options={"type": "websearch", "config": "english"})
        .limit(limit).execute()
    )
    return getattr(resp, "data", None) or []
```

- [ ] **Step 4: Rodar e ver passar**

Run: `pytest tests/test_news_repo.py -v`
Expected: PASS (todos).

- [ ] **Step 5: Commit**

```bash
git add execution/curation/news_repo.py tests/test_news_repo.py
git commit -m "feat: NewsRepo status/get/list/search (full-text)"
```

---

## Phase 2 — Ingestão re-apontada

### Task 2.1: `router._stage_only` faz upsert best-effort no Supabase

**Files:**
- Modify: `execution/curation/router.py:33-39`
- Test: `tests/test_router_supabase_upsert.py`

- [ ] **Step 1: Escrever os testes que falham**

```python
"""Tests: router stages in Redis AND upserts to Supabase (best-effort)."""
from unittest.mock import MagicMock
import pytest
import fakeredis


@pytest.fixture
def fake_redis(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    from execution.curation import redis_client
    monkeypatch.setattr(redis_client, "_get_client", lambda: fake)
    monkeypatch.setattr(redis_client, "_client", None)
    return fake


@pytest.fixture
def spy_news(monkeypatch):
    """Spy on news_repo.upsert_scraped as called from router."""
    calls = []
    from execution.curation import router
    def _fake_upsert(item_id, item):
        calls.append((item_id, item))
        return True
    monkeypatch.setattr(router.news_repo, "upsert_scraped", _fake_upsert)
    return calls


def test_route_items_upserts_each_staged_item(fake_redis, spy_news):
    from execution.curation.router import route_items
    items = [{"title": "Brazil ore up", "fullText": "x", "source": "Top News"}]
    counters, staged = route_items(items, today_date="2026-06-16", today_br="16/06/2026")
    assert counters["staged"] == 1
    assert len(spy_news) == 1
    assert spy_news[0][1]["title"] == "Brazil ore up"


def test_route_items_supabase_failure_does_not_block_staging(fake_redis, monkeypatch):
    """A Supabase outage must not stop the item reaching the Redis queue."""
    from execution.curation import router
    monkeypatch.setattr(router.news_repo, "upsert_scraped",
                        MagicMock(side_effect=RuntimeError("supabase down")))
    counters, staged = router.route_items(
        [{"title": "T", "fullText": "x", "source": "Top News"}],
        today_date="2026-06-16", today_br="16/06/2026",
    )
    assert counters["staged"] == 1
    assert fake_redis.exists("platts:staging:" + staged[0]["id"])
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pytest tests/test_router_supabase_upsert.py -v`
Expected: FAIL com `AttributeError: module 'execution.curation.router' has no attribute 'news_repo'`.

- [ ] **Step 3: Editar `router.py`**

Adicionar o import (após a linha 14 `from execution.curation.id_gen import generate_id`):

```python
from execution.curation import news_repo
```

Substituir `_stage_only` (linhas 33-39) por:

```python
def _stage_only(item: dict, item_id: str, item_type: str, today_date: str) -> dict:
    """Stage one item in Redis (load-bearing) and upsert to Supabase (best-effort).

    Redis must succeed — it backs the Telegram /queue. Supabase is best-effort
    so a transient outage never blocks fresh news from reaching the queue; the
    item still lands in Supabase next run or via the one-shot backfill.
    """
    to_stage = {**item, "id": item_id, "type": item_type}
    redis_client.set_staging(item_id, to_stage)
    redis_client.mark_seen(item_id)
    redis_client.mark_scraped(today_date, item_id)
    try:
        news_repo.upsert_scraped(item_id, to_stage)
    except Exception:
        WorkflowLogger("CurationRouter").warning(
            f"news_repo.upsert_scraped failed for {item_id} (best-effort, continuing)"
        )
    return to_stage
```

- [ ] **Step 4: Rodar e ver passar**

Run: `pytest tests/test_router_supabase_upsert.py tests/test_curation_router.py -v`
Expected: PASS (novos + os existentes do router seguem verdes).

- [ ] **Step 5: Commit**

```bash
git add execution/curation/router.py tests/test_router_supabase_upsert.py
git commit -m "feat: ingestão faz upsert best-effort de notícias no Supabase"
```

---

## Phase 3 — Curadoria grava status no Supabase

### Task 3.1: archive/reject/send_raw (single) — `callbacks_curation.py`

**Files:**
- Modify: `webhook/bot/routers/callbacks_curation.py:185-295`
- Test: `tests/test_callbacks_curation.py`

> Padrão: **Supabase primeiro (registro permanente), Redis depois (remove da fila).** Se o Supabase falhar, aborta e o item fica na fila para retry. Para de chamar `redis_client.archive` (não escreve mais `platts:archive:*`); usa `redis_client.discard`.

- [ ] **Step 1: Adicionar testes que falham** (anexar ao arquivo de testes da curadoria; criar o arquivo se não existir)

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_curate_archive_writes_supabase_then_discards_redis(mock_callback_query):
    from bot.callback_data import CurateAction
    import bot.routers.callbacks_curation as cc

    query = mock_callback_query(data="curate:archive:abc123")
    cb = CurateAction(action="archive", item_id="abc123")
    state = MagicMock()
    state.set_state = AsyncMock(); state.update_data = AsyncMock()

    with patch.object(cc.news_repo, "set_status", return_value=True) as m_status, \
         patch.object(cc.redis_client, "discard") as m_discard, \
         patch.object(cc, "_finalize_card", new=AsyncMock()):
        await cc.on_curate_action(query, cb, state)

    m_status.assert_called_once()
    assert m_status.call_args[0][1] == "archived"
    m_discard.assert_called_once_with("abc123")


@pytest.mark.asyncio
async def test_curate_archive_aborts_when_supabase_fails(mock_callback_query):
    from bot.callback_data import CurateAction
    import bot.routers.callbacks_curation as cc

    query = mock_callback_query(data="curate:archive:abc123")
    cb = CurateAction(action="archive", item_id="abc123")
    state = MagicMock()

    with patch.object(cc.news_repo, "set_status", side_effect=RuntimeError("down")), \
         patch.object(cc.redis_client, "discard") as m_discard, \
         patch.object(cc, "_finalize_card", new=AsyncMock()):
        await cc.on_curate_action(query, cb, state)

    m_discard.assert_not_called()  # não removeu da fila → dá pra tentar de novo
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pytest tests/test_callbacks_curation.py -v`
Expected: FAIL (`news_repo` não importado em callbacks_curation).

- [ ] **Step 3: Editar `callbacks_curation.py`**

Adicionar import (junto aos demais, após a linha 26 `from execution.curation import redis_client`):

```python
from execution.curation import news_repo
```

Substituir o ramo `if action == "archive":` (linhas 185-201) por:

```python
    if action == "archive":
        try:
            updated = await asyncio.to_thread(
                news_repo.set_status, item_id, "archived", chat_id=chat_id,
            )
        except Exception as exc:
            logger.error(f"curate_archive supabase error: {exc}")
            await query.answer("⚠️ Supabase indisponível, tenta de novo")
            return
        if not updated:
            await query.answer("⚠️ Item não encontrado no banco")
            await _finalize_card(query, "⚠️ Item não encontrado no banco")
            return
        try:
            await asyncio.to_thread(redis_client.discard, item_id)
        except Exception as exc:
            logger.warning(f"discard after archive failed for {item_id}: {exc}")
        await query.answer("✅ Arquivado")
        await _finalize_card(
            query,
            f"✅ *Arquivado*\n🕒 {datetime.now(timezone.utc).strftime('%H:%M')} UTC · 🆔 `{item_id}`",
        )
```

No ramo `elif action == "reject":`, substituir a chamada de discard (linhas 211-216) para gravar o status antes:

```python
        try:
            await asyncio.to_thread(news_repo.set_status, item_id, "rejected")
            await asyncio.to_thread(redis_client.discard, item_id)
        except Exception as exc:
            logger.error(f"curate_reject error: {exc}")
            await query.answer("⚠️ Indisponível")
            return
```

No ramo `elif action == "send_raw":`, substituir o bloco "Archive the item" (linhas 282-287) por:

```python
        # Mark archived in Supabase + remove from Redis queue
        try:
            await asyncio.to_thread(news_repo.set_status, item_id, "archived", chat_id=chat_id)
            await asyncio.to_thread(redis_client.discard, item_id)
        except Exception as exc:
            logger.warning(f"archive after send_raw failed: {exc}")
```

- [ ] **Step 4: Rodar e ver passar**

Run: `pytest tests/test_callbacks_curation.py tests/test_callbacks_curation_reports.py -v`
Expected: PASS (novos + existentes).

- [ ] **Step 5: Commit**

```bash
git add webhook/bot/routers/callbacks_curation.py tests/test_callbacks_curation.py
git commit -m "feat: curadoria grava status no Supabase (archive/reject/send_raw)"
```

### Task 3.2: bulk archive/discard — `callbacks_queue.py`

**Files:**
- Modify: `webhook/bot/routers/callbacks_queue.py:236-266`
- Test: `tests/test_callbacks_queue.py`

- [ ] **Step 1: Adicionar testes que falham**

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_bulk_confirm_archive_writes_supabase_then_redis(mock_callback_query):
    from bot.callback_data import QueueBulkConfirm
    import bot.routers.callbacks_queue as cq

    query = mock_callback_query(data="q_bulkok:archive")
    cb = QueueBulkConfirm(action="archive")

    with patch.object(cq.queue_selection, "is_select_mode", return_value=True), \
         patch.object(cq.queue_selection, "get_selection", return_value={"a", "b"}), \
         patch.object(cq.queue_selection, "exit_mode"), \
         patch.object(cq.news_repo, "set_status_bulk", return_value=2) as m_status, \
         patch.object(cq.curation_redis, "bulk_discard", return_value=2) as m_discard, \
         patch.object(cq, "_rerender", new=AsyncMock()):
        await cq.on_queue_bulk_confirm(query, cb)

    assert m_status.call_args[0][1] == "archived"
    m_discard.assert_called_once()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pytest tests/test_callbacks_queue.py -v`
Expected: FAIL (`news_repo` não importado em callbacks_queue).

- [ ] **Step 3: Editar `callbacks_queue.py`**

Adicionar import (após a linha 25 `from execution.curation import redis_client as curation_redis`):

```python
from execution.curation import news_repo
```

Substituir o ramo `if callback_data.action == "archive":` (linhas 236-256) por:

```python
    if callback_data.action == "archive":
        try:
            updated = await asyncio.to_thread(
                news_repo.set_status_bulk, ids, "archived", chat_id=chat_id,
            )
        except Exception as exc:
            logger.error(f"bulk archive supabase failed: {exc}")
            await query.answer("⚠️ Erro ao arquivar (Supabase)")
            await _rerender(query, page=1)
            return
        try:
            deleted = await asyncio.to_thread(curation_redis.bulk_discard, ids)
        except Exception as exc:
            logger.warning(f"bulk_discard after archive failed: {exc}")
            deleted = updated
        ok = int(updated)
        ok_word = "arquivado" if ok == 1 else "arquivados"
        toast = f"✅ {ok} {ok_word}" if ok else "⚠️ Nenhum item arquivado"
```

- [ ] **Step 4: Rodar e ver passar**

Run: `pytest tests/test_callbacks_queue.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add webhook/bot/routers/callbacks_queue.py tests/test_callbacks_queue.py
git commit -m "feat: bulk archive grava status no Supabase"
```

---

## Phase 4 — Leituras re-apontadas para o Supabase

### Task 4.1: `redis_queries.list_archive_recent` + `stats_for_date` (archived) delegam ao NewsRepo

**Files:**
- Modify: `webhook/redis_queries.py:73-101` e `:196-226`
- Test: `tests/test_redis_queries.py`

- [ ] **Step 1: Adicionar testes que falham**

```python
from unittest.mock import patch


def test_list_archive_recent_delegates_to_news_repo():
    import redis_queries
    with patch("redis_queries.news_repo.list_by_status",
               return_value=[{"id": "a", "title": "T", "archived_at": "2026-06-16T00:00:00+00:00"}]) as m:
        out = redis_queries.list_archive_recent(limit=5)
    m.assert_called_once_with("archived", limit=5)
    assert out[0]["id"] == "a"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pytest tests/test_redis_queries.py::test_list_archive_recent_delegates_to_news_repo -v`
Expected: FAIL (`news_repo` não importado em redis_queries).

- [ ] **Step 3: Editar `redis_queries.py`**

Adicionar import no topo (após a linha 21 `from typing import Optional`):

```python
from execution.curation import news_repo
```

Substituir `list_archive_recent` (linhas 73-101) por:

```python
def list_archive_recent(limit: int = 10) -> list[dict]:
    """Return archived news newest-first from Supabase (platts_news)."""
    return news_repo.list_by_status("archived", limit=limit)
```

Em `stats_for_date` (linha 206), trocar a contagem de archived no Redis pela do Supabase. Substituir:

```python
    archived = sum(1 for _ in client.scan_iter(match=f"platts:archive:{date_iso}:*", count=200))
```

por:

```python
    try:
        archived = len(news_repo.list_by_status("archived", limit=10000))
    except Exception:
        archived = 0
```

- [ ] **Step 4: Rodar e ver passar**

Run: `pytest tests/test_redis_queries.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add webhook/redis_queries.py tests/test_redis_queries.py
git commit -m "feat: leituras de archive (/history, stats) leem do Supabase"
```

### Task 4.2: reprocess / preview / mini-app leem via `news_repo.get_by_id`

**Files:**
- Modify: `webhook/bot/routers/_helpers.py:69-81`, `webhook/routes/preview.py:19-39`, `webhook/routes/mini_api.py:243,291-295`

- [ ] **Step 1: `_helpers.py`** — onde hoje faz `item = redis_client.get_archive(date, item_id)` (linha 81), trocar a estratégia: tenta staging no Redis; se não houver, busca no Supabase por id. Substituir o bloco que monta `item` a partir do archive por:

```python
        from execution.curation import news_repo
        item = redis_client.get_staging(item_id)
        if item is None:
            item = news_repo.get_by_id(item_id)
```

(Remover o loop `for date in (...)` que varria archive datado — `get_by_id` não precisa de data.)

- [ ] **Step 2: `preview.py`** — substituir o bloco (linhas ~30-39) que tenta `redis_client.get_archive(date, item_id)` por:

```python
        from execution.curation import news_repo
        item = redis_client.get_staging(item_id)
        if item is None:
            item = news_repo.get_by_id(item_id)
```

- [ ] **Step 3: `mini_api.py`** — em `list` (linha 243) já chama `redis_queries.list_archive_recent` (auto-corrigido pela Task 4.1). Nas linhas 291-295, substituir a tentativa today/yesterday de `redis_client.get_archive` por:

```python
        from execution.curation import news_repo
        item = await asyncio.to_thread(news_repo.get_by_id, item_id)
```

- [ ] **Step 4: Rodar a suíte de leitura**

Run: `pytest tests/test_callbacks_reports.py tests/test_mini_reports.py tests/test_mini_news.py tests/test_platts_reports_trace.py -v`
Expected: PASS (ajustar mocks que esperavam `get_archive` para `news_repo.get_by_id`).

- [ ] **Step 5: Commit**

```bash
git add webhook/bot/routers/_helpers.py webhook/routes/preview.py webhook/routes/mini_api.py tests/
git commit -m "feat: reprocess/preview/mini-app leem notícia do Supabase por id"
```

---

## Phase 5 — Carga única dos 243 itens

### Task 5.1: Script de backfill `platts:archive:*` → `platts_news`

**Files:**
- Create: `execution/scripts/migrate_archive_to_supabase.py`
- Test: `tests/test_migrate_archive_to_supabase.py`

- [ ] **Step 1: Escrever o teste que falha**

```python
"""Tests for the one-shot archive→Supabase backfill."""
from unittest.mock import MagicMock
import json
import pytest
import fakeredis


@pytest.fixture
def fake_redis(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    from execution.curation import redis_client
    monkeypatch.setattr(redis_client, "_get_client", lambda: fake)
    monkeypatch.setattr(redis_client, "_client", None)
    return fake


def test_backfill_inserts_each_archive_item(fake_redis, monkeypatch):
    from execution.scripts import migrate_archive_to_supabase as mig
    # seed two archive keys
    fake_redis.set("platts:archive:2026-06-15:a",
                   json.dumps({"id": "a", "title": "A", "fullText": "x",
                               "archivedAt": "2026-06-15T10:00:00+00:00", "archivedBy": 5}))
    fake_redis.set("platts:archive:2026-06-15:b",
                   json.dumps({"id": "b", "title": "B"}))
    rows = []
    fake_client = MagicMock()
    fake_client.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[{"id": "ok"}])
    def _capture(row, **kw):
        rows.append(row)
        return fake_client.table.return_value.upsert.return_value
    fake_client.table.return_value.upsert.side_effect = _capture
    monkeypatch.setattr(mig, "get_news_client", lambda: fake_client)

    count = mig.backfill()
    assert count == 2
    ids = {r["id"] for r in rows}
    assert ids == {"a", "b"}
    a_row = next(r for r in rows if r["id"] == "a")
    assert a_row["status"] == "archived"
    assert a_row["archived_at"] == "2026-06-15T10:00:00+00:00"
    assert a_row["archived_by"] == 5
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pytest tests/test_migrate_archive_to_supabase.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implementar o script**

```python
#!/usr/bin/env python3
"""Carga única: platts:archive:* (Redis) → platts_news (Supabase).

Idempotente: usa upsert ON CONFLICT DO NOTHING, então pode rodar N vezes.
Roda manualmente após a migração SQL estar aplicada no projeto de notícias.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from execution.curation import redis_client
from execution.curation.news_repo import _item_to_row, TABLE
from execution.integrations.news_supabase_client import get_news_client


def _archive_row(key: str, data: dict) -> dict:
    """Build a platts_news row (status=archived) from an archive payload."""
    item_id = data.get("id") or key.split(":")[-1]
    row = _item_to_row(item_id, data, status="archived")
    if data.get("archivedAt"):
        row["archived_at"] = data["archivedAt"]
    if data.get("archivedBy") is not None:
        row["archived_by"] = data["archivedBy"]
    if data.get("stagedAt"):
        row["scraped_at"] = data["stagedAt"]
    return row


def backfill() -> int:
    """Read all platts:archive:* keys and upsert them. Returns count inserted/seen."""
    client = redis_client._get_client()
    sb = get_news_client()
    count = 0
    for key in client.scan_iter(match="platts:archive:*", count=500):
        raw = client.get(key)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        row = _archive_row(key, data)
        sb.table(TABLE).upsert(row, on_conflict="id", ignore_duplicates=True).execute()
        count += 1
    return count


if __name__ == "__main__":
    n = backfill()
    print(f"✅ Backfill concluído: {n} itens processados")
```

- [ ] **Step 4: Rodar e ver passar**

Run: `pytest tests/test_migrate_archive_to_supabase.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add execution/scripts/migrate_archive_to_supabase.py tests/test_migrate_archive_to_supabase.py
git commit -m "feat: script de carga única archive→Supabase"
```

### Task 5.2: Executar o backfill (após coordenadas + migração aplicada)

> **Bloqueado por:** Task 0.2 (migração aplicada) + env `NEWS_SUPABASE_*` setadas.

- [ ] **Step 1: Rodar o backfill contra produção**

Run: `source .venv/bin/activate && python -m execution.scripts.migrate_archive_to_supabase`
Expected: `✅ Backfill concluído: 243 itens processados` (±, conforme o estado do Redis).

- [ ] **Step 2: Conferir no Supabase**

Run (SQL Editor): `select status, count(*) from platts_news group by status;`
Expected: ~243 em `archived`.

---

## Phase 6 — Env vars & CI

### Task 6.1: Documentar e cablear `NEWS_SUPABASE_*`

**Files:**
- Modify: `.env.example`, `.github/workflows/market_news.yml`

- [ ] **Step 1: `.env.example`** — adicionar (na seção Supabase):

```bash
# Projeto Supabase DEDICADO a notícias (separado do banco de trading)
NEWS_SUPABASE_URL=https://<news-project-ref>.supabase.co
NEWS_SUPABASE_SERVICE_KEY=
```

- [ ] **Step 2: `market_news.yml`** — no bloco `env:` do step "Run Platts Ingestion" (após as linhas de `SUPABASE_*`, ~linha 64), adicionar:

```yaml
          NEWS_SUPABASE_URL: ${{ secrets.NEWS_SUPABASE_URL }}
          NEWS_SUPABASE_SERVICE_KEY: ${{ secrets.NEWS_SUPABASE_SERVICE_KEY }}
```

- [ ] **Step 3: Setar os secrets no GitHub** (manual)

Run: `gh secret set NEWS_SUPABASE_URL` e `gh secret set NEWS_SUPABASE_SERVICE_KEY`
Expected: "✓ Set secret".

- [ ] **Step 4: Setar `.env` local** (não commitado) com os mesmos valores.

- [ ] **Step 5: Commit**

```bash
git add .env.example .github/workflows/market_news.yml
git commit -m "chore: env vars NEWS_SUPABASE_* para ingestão e CI"
```

### Task 6.2: Suíte completa verde

- [ ] **Step 1: Rodar tudo**

Run: `source .venv/bin/activate && pytest -q`
Expected: toda a suíte passa (sem regressão nas leituras/curadoria).

- [ ] **Step 2: Commit (se houver ajustes de mocks remanescentes)**

```bash
git add tests/
git commit -m "test: ajustes de mocks para leituras via Supabase"
```

---

## Out of scope (v1)

- Embeddings / busca semântica (`pgvector`) — ponto de extensão deferido (coluna `embedding vector(N)` + índice ivfflat/hnsw).
- Cron de `expired` (marcar `staged` com `scraped_at > 2d`) — default desligado; pode virar uma task futura.
- Remoção das funções não usadas `redis_client.archive`/`get_archive`/`bulk_archive` — deixar como dead code controlado nesta entrega; limpar num PR de hygiene posterior.
- Migração de `webhook:feedback:*` para Supabase.
```
