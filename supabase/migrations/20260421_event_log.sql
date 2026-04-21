-- Phase 1 observability: event_log table
-- Referenced by execution/core/event_bus.py _SupabaseSink and webhook/bot/routers/commands.py /tail (Phase 4).

CREATE TABLE IF NOT EXISTS event_log (
  id            BIGSERIAL PRIMARY KEY,
  ts            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  workflow      TEXT NOT NULL,
  run_id        TEXT NOT NULL,
  trace_id      TEXT,
  parent_run_id TEXT,
  level         TEXT NOT NULL CHECK (level IN ('info', 'warn', 'error')),
  event         TEXT NOT NULL,
  label         TEXT,
  detail        JSONB,
  pod           TEXT
);

CREATE INDEX IF NOT EXISTS idx_event_log_workflow_ts ON event_log (workflow, ts DESC);
CREATE INDEX IF NOT EXISTS idx_event_log_run_id      ON event_log (run_id);
CREATE INDEX IF NOT EXISTS idx_event_log_trace_id    ON event_log (trace_id);

-- TTL cleanup: rows older than 30 days deleted nightly.
-- If pg_cron is not enabled on this Supabase project, defer this to a manual cleanup
-- script (see Phase 4 followup). For now, leave as a comment for operator awareness:
-- SELECT cron.schedule('event_log_ttl', '0 3 * * *',
--   $$DELETE FROM event_log WHERE ts < NOW() - INTERVAL '30 days'$$);
