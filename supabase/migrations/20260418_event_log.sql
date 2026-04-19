-- Phase 3 event_log: timeline storage for workflow/draft observability.
-- Populated by execution/core/progress_reporter.py:ProgressReporter.step()
-- Queried for per-draft/per-run timelines.

create table if not exists event_log (
  id bigserial primary key,
  workflow text not null,
  run_id text,
  draft_id text,
  level text not null check (level in ('info', 'warning', 'error')),
  label text not null,
  detail text,
  context jsonb default '{}'::jsonb,
  created_at timestamptz default now()
);

create index if not exists event_log_draft_idx
  on event_log (draft_id) where draft_id is not null;

create index if not exists event_log_workflow_time_idx
  on event_log (workflow, created_at desc);

create index if not exists event_log_run_idx
  on event_log (run_id) where run_id is not null;

comment on table event_log is
  'Observability timeline events written by ProgressReporter.step(). '
  'Retention: TBD; truncate policy lives outside this migration.';
