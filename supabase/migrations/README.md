# Supabase Migrations

SQL migration files named `YYYYMMDD_<name>.sql`, applied manually to the Supabase project.

## How to apply

1. Log into Supabase dashboard for the target project.
2. Open the SQL editor.
3. Paste the migration file contents.
4. Run. Verify with: `select count(*) from <new_table>;`

Or via Supabase CLI:
```bash
supabase db push --project-ref <PROJECT_REF>
```

## Conventions

- Filename: `YYYYMMDD_<short_name>.sql` (ISO date prefix keeps natural ordering).
- SQL uses `create ... if not exists` so re-applies are idempotent.
- Indexes and comments in the same file as the table creation.
- No `drop` statements without explicit rollback policy.

## Applied migrations

| File | Applied to dev | Applied to prod | Notes |
|---|---|---|---|
| `20260418_event_log.sql` | ✅ 2026-04-19 | ✅ 2026-04-19 | Phase 3 — event_log table for ProgressReporter observability |
| `20260419_event_log_rls.sql` | ✅ 2026-04-19 | ✅ 2026-04-19 | Phase 3 follow-up — enable RLS on event_log (service-role writes continue; no anon reads) |
