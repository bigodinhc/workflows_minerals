-- Phase: OneDrive PDF → WhatsApp broadcast workflow (2026-04-23)
-- Adds contact_lists + contact_list_members for admin-selectable broadcast targets.
-- Related spec: docs/superpowers/specs/2026-04-22-onedrive-pdf-broadcast-design.md

create table if not exists contact_lists (
  code        text        primary key,
  label       text        not null,
  description text,
  created_at  timestamptz not null default now()
);

create table if not exists contact_list_members (
  list_code     text        not null references contact_lists(code) on delete cascade,
  contact_phone text        not null references contacts(phone_uazapi) on delete cascade,
  created_at    timestamptz not null default now(),
  primary key (list_code, contact_phone)
);

create index if not exists contact_list_members_list_code_idx on contact_list_members(list_code);

comment on table contact_lists is
  'Admin-selectable broadcast targets for OneDrive PDF → WhatsApp workflow. '
  'Rows define named audiences; see contact_list_members for membership.';
comment on column contact_lists.code is
  'Stable slug used as PK and in admin UI. e.g. minerals_report.';
comment on column contact_lists.label is
  'Human-readable name shown in bot approval card buttons.';
comment on table contact_list_members is
  'Junction: which contacts belong to which broadcast list. '
  'Cascade deletes follow contact removal or list removal.';

alter table contact_lists        enable row level security;
alter table contact_list_members enable row level security;
-- No policies: service_role bypasses RLS; all access is service-role.

insert into contact_lists (code, label) values
  ('minerals_report', 'Minerals Report'),
  ('solid_fuels',     'Solid Fuels'),
  ('time_interno',    'Time Interno')
on conflict (code) do nothing;
