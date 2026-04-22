-- Phase 1 observability: event_log table
-- Referenced by execution/core/event_bus.py _SupabaseSink and webhook/bot/routers/commands.py /tail (Phase 4).
--
-- Idempotent: safe to apply whether the remote table is missing, matches, or diverges.
--   A. If table missing: CREATE TABLE IF NOT EXISTS builds it with our full schema.
--   B. If table exists with different columns (e.g. from an earlier prototype): ALTERs add any missing columns.
--   C. If table matches: every statement is a no-op.
--
-- Constraints deliberately relaxed (no NOT NULL, no CHECK (level IN (...))) to stay
-- compatible with pre-existing rows from an earlier migration (20260416042250) whose
-- content we haven't audited. The event_bus module always emits non-null workflow /
-- run_id / event / level, so new rows are well-formed. Tighten in Phase 4 after audit.

CREATE TABLE IF NOT EXISTS event_log (
  id            BIGSERIAL PRIMARY KEY,
  ts            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  workflow      TEXT,
  run_id        TEXT,
  trace_id      TEXT,
  parent_run_id TEXT,
  level         TEXT,
  event         TEXT,
  label         TEXT,
  detail        JSONB,
  pod           TEXT
);

ALTER TABLE event_log ADD COLUMN IF NOT EXISTS ts            TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE event_log ADD COLUMN IF NOT EXISTS workflow      TEXT;
ALTER TABLE event_log ADD COLUMN IF NOT EXISTS run_id        TEXT;
ALTER TABLE event_log ADD COLUMN IF NOT EXISTS trace_id      TEXT;
ALTER TABLE event_log ADD COLUMN IF NOT EXISTS parent_run_id TEXT;
ALTER TABLE event_log ADD COLUMN IF NOT EXISTS level         TEXT;
ALTER TABLE event_log ADD COLUMN IF NOT EXISTS event         TEXT;
ALTER TABLE event_log ADD COLUMN IF NOT EXISTS label         TEXT;
ALTER TABLE event_log ADD COLUMN IF NOT EXISTS detail        JSONB;
ALTER TABLE event_log ADD COLUMN IF NOT EXISTS pod           TEXT;

CREATE INDEX IF NOT EXISTS idx_event_log_workflow_ts ON event_log (workflow, ts DESC);
CREATE INDEX IF NOT EXISTS idx_event_log_run_id      ON event_log (run_id);
CREATE INDEX IF NOT EXISTS idx_event_log_trace_id    ON event_log (trace_id);

-- TTL cleanup: rows older than 30 days deleted nightly.
-- If pg_cron is not enabled on this Supabase project, defer to a manual cleanup
-- script (see Phase 4 followup). Left as a comment for operator awareness:
-- SELECT cron.schedule('event_log_ttl', '0 3 * * *',
--   $$DELETE FROM event_log WHERE ts < NOW() - INTERVAL '30 days'$$);
