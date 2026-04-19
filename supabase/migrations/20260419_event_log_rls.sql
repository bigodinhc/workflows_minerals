-- Phase 3 follow-up: enable RLS on event_log.
--
-- Why separate file: the original 20260418_event_log.sql has already been applied
-- to the Supabase project. Adding RLS statements to that file would not re-apply;
-- this new file is the operational delta.
--
-- Effect: enables row-level security. No policies = deny-all for anon and
-- authenticated roles. Service-role key bypasses RLS via design, so the
-- ProgressReporter.step() inserts continue working unchanged.
--
-- To open read access later (e.g., dashboard showing event timeline to a user
-- scoped by user_id in context->>'user_id'), add a targeted policy in a
-- separate migration.

alter table event_log enable row level security;

comment on table event_log is
  'Observability timeline events written by ProgressReporter.step(). '
  'Retention: TBD; truncate policy lives outside this migration. '
  'RLS enabled since 2026-04-19 — service-role writes bypass; no anon access.';
