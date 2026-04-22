-- Phase: contacts migration from Google Sheets to Supabase
-- Replaces: gspread reads of sheet 1tU3Izdo21JichTXg15bc1paWUiN8XioJYZUPpbIUgL0 / 'Página1'
-- Consumers: execution/scripts/*, webhook/dispatch.py, webhook/bot/routers/*, dashboard/api/contacts

create table if not exists contacts (
  id           uuid        primary key default gen_random_uuid(),
  name         text        not null,
  phone_raw    text        not null,
  phone_uazapi text        not null,
  status       text        not null default 'ativo'
                 check (status in ('ativo', 'inativo')),
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

create unique index contacts_phone_uazapi_uidx on contacts (phone_uazapi);
create index        contacts_status_idx        on contacts (status);
create index        contacts_status_active_idx on contacts (created_at desc)
  where status = 'ativo';

create or replace function contacts_set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists contacts_updated_at on contacts;
create trigger contacts_updated_at
  before update on contacts
  for each row execute function contacts_set_updated_at();

alter table contacts enable row level security;
-- No policies. Only service_role bypasses RLS; anon/public get zero access.

comment on table contacts is
  'WhatsApp broadcast list. Source of truth for all agentic workflows. '
  'Replaced Google Sheets (1tU3Izdo21JichTXg15bc1paWUiN8XioJYZUPpbIUgL0) on 2026-04-22.';
comment on column contacts.phone_raw is
  'What the user typed at /add, before normalization. Audit trail.';
comment on column contacts.phone_uazapi is
  'Digits-only E.164 without +, ready for uazapi number field. e.g. 5511987654321';
comment on column contacts.status is
  'ativo = receives broadcasts. inativo = suppressed but preserved, never deleted.';
