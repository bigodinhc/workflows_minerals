# Scrap Dedup Fix — Design Spec

## Problem

Platts scraper produces duplicate items in Redis staging/archive. Root cause is two compounding bugs:

**H1 — Date-scoped dedup:** `platts:seen:<date>` is a per-day Redis SET. Items seen yesterday exist in `platts:seen:2026-04-14` but today's check queries `platts:seen:2026-04-15` (empty) — item re-enters the pipeline. Evidence: 12 overlapping IDs between 14/abr and 15/abr seen sets; 7 items archived on both days.

**H2 — Source-dependent ID:** `generate_id(source, title)` includes the scraper tab/page name in the hash. The same Platts article appears in multiple pages (e.g., "Latest" and "Top News - Ferrous Metals"), producing different IDs for the same logical item. Evidence: "EU reaches deal on steel safeguards..." generated 4 archive entries (2 IDs x 2 days).

## Goal

Eliminate duplicate items in staging and archive. A given logical article (same title) must appear at most once in the pipeline, regardless of which Platts page it was scraped from or how many days it persists on the site.

## Approach — Option A (approved)

Three coordinated changes: canonical ID by title only, global seen-set, staging short-circuit.

## Architecture

### 1. Canonical ID by normalized title

**File:** `execution/curation/id_gen.py`

Current signature: `generate_id(source: str, title: str) -> str`
New signature: `generate_id(title: str) -> str`

New public function: `normalize_title(title: str) -> str`

`normalize_title` steps:
1. `strip()` leading/trailing whitespace
2. `lower()`
3. Collapse internal whitespace (`\s+` -> single space)
4. Normalize curly quotes to straight quotes (`\u2018\u2019` -> `'`, `\u201c\u201d` -> `"`)
5. Strip trailing sentence punctuation (`. , ;` at end only)

Conservative: no accent folding, no aggressive punctuation removal. Two titles that a human would read as "different" must remain different.

`generate_id` raises `ValueError` if normalized title is empty (after strip). Router catches and logs warning, skips the item.

Hash: `sha256(normalized_title)[:12]` (hex). Same truncation as today.

### 2. Global seen-set

**File:** `execution/curation/redis_client.py`

Replace per-day `platts:seen:<date>` (SET) with single `platts:seen` (Sorted Set).

| Operation | Before | After |
|-----------|--------|-------|
| Mark seen | `SADD platts:seen:<date> <id>` + `EXPIRE 30d` | `ZADD platts:seen <epoch_ts> <id>` |
| Check seen | `SISMEMBER platts:seen:<date> <id>` | `ZSCORE platts:seen <id>` (non-null = seen) |
| Prune | Implicit via key TTL | `ZREMRANGEBYSCORE platts:seen -inf <now - 30d>` before each `mark_seen` call |
| Stats count | `SCARD platts:seen:<date>` | `SCARD platts:scraped:<date>` (new key, see below) |

Function signatures change:
- `is_seen(item_id: str) -> bool` — removes `date` parameter
- `mark_seen(item_id: str) -> None` — removes `date` parameter, adds prune step

Retention: 30 days (constant `_SEEN_RETENTION_SECONDS = 30 * 86400`). Prune runs in `mark_seen` (O(log N + M), M = expired members — negligible for ~1000 items/month).

### 3. Scraped-per-day telemetry key

**File:** `execution/curation/redis_client.py`

New key: `platts:scraped:<date>` (SET, TTL 30d). Populated alongside `mark_seen` in router. Used by `stats_for_date` for the "Scraped" count.

This separates concerns: `platts:seen` is for dedup (global, rolling), `platts:scraped:<date>` is for daily metrics (dated, disposable).

### 4. Staging short-circuit

**File:** `execution/curation/redis_client.py`

New function: `staging_exists(item_id: str) -> bool` — `EXISTS platts:staging:<id>`.

**File:** `execution/curation/router.py`

Check order in `route_items`:
```
id = generate_id(item["title"])
if staging_exists(id):      # fast: item still queued
    skipped_staged += 1
    continue
if is_seen(id):              # global dedup
    skipped_seen += 1
    continue
stage(id) + mark_seen(id) + mark_scraped(date, id)
```

New counter key: `skipped_staged` in counters dict returned by `route_items`.

### 5. Migration script

**File:** `execution/scripts/rebuild_dedup.py` (new)

One-shot, run manually after deploy:
1. SCAN `platts:archive:*` — collect all archived items
2. For each, compute `new_id = generate_id(title)` with new formula
3. `ZADD platts:seen <now> <new_id>` via pipeline (bulk)
4. DEL all `platts:seen:<date>` keys (old format)
5. No archive key renaming (archives keep old IDs — cosmetic only, no user-visible impact)

Flags:
- `--dry-run` (default): print counts, no mutations
- `--execute`: apply changes

Idempotent: running twice produces same result.

## Files changed

| File | Change |
|------|--------|
| `execution/curation/id_gen.py` | New `normalize_title`, change `generate_id` signature |
| `execution/curation/redis_client.py` | Global seen-set, `staging_exists`, `mark_scraped`, remove date params |
| `execution/curation/router.py` | Update `generate_id` call, add staging short-circuit, update `is_seen`/`mark_seen` calls |
| `webhook/redis_queries.py` | Update `stats_for_date` to use `platts:scraped:<date>` instead of `platts:seen:<date>` |
| `execution/scripts/rebuild_dedup.py` | New migration script |
| `tests/test_curation_id_gen.py` | Update for new signature + normalize tests |
| `tests/test_curation_redis_client.py` | Update for new signatures + global seen tests |
| `tests/test_curation_router.py` | H1/H2 regression tests, staging short-circuit, new counters |
| `tests/test_redis_queries.py` | Update stats test for `platts:scraped` key |

## Error handling

- `generate_id("")` or `generate_id("   ")` raises `ValueError`. Router catches, logs warning, skips item. Does not abort the scrape run.
- `mark_seen` prune failure (Redis error): log warning, continue. Prune is best-effort — dedup still works, just accumulates stale entries until next successful prune.
- Concurrent scrape runs (unlikely — Apify cron is serial): `ZADD` and `EXISTS` are atomic. Worst case: item staged twice = same key overwritten. No corruption.
- Staging TTL expiry (48h): if item sits unprocessed for 48h, staging key expires, `staging_exists` returns False, item may re-enter queue on next scrape. Acceptable — means operator didn't act in 48h, treat as new.

## Deploy & migration sequence

1. Push code to main (Railway auto-deploys)
2. Wait for deploy SUCCESS
3. Run `python execution/scripts/rebuild_dedup.py --dry-run` via `railway run`
4. Verify counts look right
5. Run `python execution/scripts/rebuild_dedup.py --execute`
6. Verify: `redis-cli ZCARD platts:seen` matches expected count
7. Trigger manual Apify scrape — digest should show near-zero new items (everything already "seen" from migration)
8. Next automatic scrape confirms only genuinely new articles appear

## Testing

### Unit tests (TDD, fakeredis)

**id_gen:**
- `normalize_title` transforms: whitespace collapse, lowercase, curly quotes, trailing punctuation
- `generate_id(title)` deterministic across calls
- Same title with different whitespace/case/quotes produces same ID
- Different titles produce different IDs
- Empty/whitespace-only title raises ValueError

**redis_client:**
- `mark_seen(id)` then `is_seen(id)` returns True
- `is_seen(unknown)` returns False
- `mark_seen(id)` twice: ZCARD = 1 (idempotent)
- Prune: item with score 31 days old is removed after next `mark_seen`
- `staging_exists(id)` True after `set_staging`, False after DEL
- `mark_scraped(date, id)` populates `platts:scraped:<date>`

**router:**
- Staging short-circuit: existing staging item skipped, counted in `skipped_staged`
- Seen short-circuit: seen item skipped, counted in `skipped_seen`
- Same title different source: 1 staging entry only (H2 regression)
- Same title across simulated days: 2nd run skips (H1 regression)
- Counters balance: `total == staged + skipped_seen + skipped_staged`

**redis_queries:**
- `stats_for_date` reads `platts:scraped:<date>` for scraped count

**migration script:**
- Dry-run produces no mutations
- Execute populates global seen, deletes dated keys
- Idempotent: second run is no-op

### Manual production validation

1. `rebuild_dedup.py --dry-run` output reviewed
2. Post-migration `ZCARD platts:seen` matches archive count
3. Manual Apify trigger produces near-empty digest
4. Next-day automatic scrape: only new articles, no duplicates
5. `/stats` scraped count works correctly

## Out of scope

- Archive key renaming (old IDs stay, no user-visible impact)
- Feature flags / gradual rollout (migration is seconds, deploy window is sufficient)
- Duplicate detection by URL/href (future enhancement if title-based dedup proves insufficient)
- Prompts adjustment for rationale (separate spec)
