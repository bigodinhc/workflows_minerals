-- Phase: notícias Platts migradas de Redis (platts:staging/archive) para Postgres.
-- Projeto: liqiwvueesohlnnmezyw (antigravity-reports) — mesmo projeto que o repo já gerencia.
-- Consumers: execution/curation/router.py (ingestão), webhook/bot/routers/* (curadoria),
--            webhook/redis_queries.py, webhook/routes/{preview,mini_api}.py, copilot (busca full-text).

create table if not exists platts_news (
  id            text        primary key,
  type          text        not null default 'news',
  status        text        not null default 'staged'
                  check (status in ('staged','archived','rejected','expired')),
  title         text        not null,
  href          text,
  source        text,
  author        text,
  publish_date         text,
  publish_date_parsed  date,
  full_text     text,
  paragraphs    jsonb,
  tables        jsonb,
  metadata      jsonb,
  raw           jsonb,
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
  'Notícias Platts (iron ore). status: staged=na fila/não curada, archived=curada, rejected=recusada, expired=saiu da fila sem curadoria. Substituiu platts:archive:* do Redis em 2026-06-16.';
comment on column platts_news.id is 'sha256(título normalizado)[:12] — mesma chave de dedup do Redis.';
comment on column platts_news.raw is 'Objeto JSON original do scraper, preservado por garantia.';
