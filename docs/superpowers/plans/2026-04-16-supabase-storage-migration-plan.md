# Supabase Storage Migration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Spec reference:** `docs/superpowers/specs/2026-04-16-supabase-storage-migration-design.md`
>
> **Supabase MCP tools available:** `mcp__supabase__execute_sql`, `mcp__supabase__apply_migration`, `mcp__supabase__list_tables`, `mcp__claude_ai_Supabase__get_project`, `mcp__claude_ai_Supabase__get_project_url`, `mcp__claude_ai_Supabase__get_publishable_keys`

**Goal:** Replace Google Drive + Redis dedup with Supabase Storage + Postgres in the `platts-scrap-reports` actor. Change Telegram distribution from N documents to 1 summary message with inline download buttons. Add webhook callback handler for on-demand PDF delivery.

**Architecture:** PDFs stored in Supabase Storage bucket `platts-reports` (private, signed URLs). Metadata in Postgres table `platts_reports` with UNIQUE(slug, date_key) for dedup. Actor sends 1 Telegram summary after all downloads. Webhook handles `report_dl:<uuid>` callbacks: fetch signed URL → sendDocument.

**Tech Stack:** `@supabase/supabase-js` (actor, JS), `supabase` Python client (webhook), Supabase Storage + Postgres 17.

---

## File Structure

**Actor (`actors/platts-scrap-reports/`):**
- Create: `src/persist/supabaseClient.js` — singleton Supabase client
- Create: `src/persist/supabaseUpload.js` — replaces `gdriveUpload.js`
- Create: `src/notify/telegramSummary.js` — replaces per-PDF sends with 1 summary message
- Modify: `src/main.js` — rewire imports, remove Redis/Drive, new flow
- Modify: `package.json` — swap deps
- Modify: `.actor/input_schema.json` — remove `gdriveFolderId`, update descriptions
- Delete: `src/persist/gdriveUpload.js`
- Delete: `src/persist/redisDedup.js`

**Webhook (`webhook/`):**
- Modify: `webhook/app.py` — add `report_dl:<uuid>` callback handler (~30 lines)
- Modify: `webhook/requirements.txt` — add `supabase`

**Infra (Supabase):**
- Migration: CREATE TABLE `platts_reports`
- Storage: CREATE bucket `platts-reports` (private)

---

## Task 1: Create Supabase table + bucket via MCP

**Files:** None (Supabase infra only)

- [ ] **Step 1: Apply migration for `platts_reports` table**

Use MCP tool `mcp__supabase__apply_migration` with project ID `liqiwvueesohlnnmezyw`:

```sql
CREATE TABLE platts_reports (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    slug TEXT NOT NULL,
    date_key DATE NOT NULL,
    report_name TEXT NOT NULL,
    report_type TEXT NOT NULL,
    frequency TEXT,
    cover_date TEXT,
    published_date TEXT,
    storage_path TEXT NOT NULL,
    file_size_bytes BIGINT,
    telegram_message_id BIGINT,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(slug, date_key)
);
```

- [ ] **Step 2: Create the storage bucket**

Use MCP tool `mcp__supabase__execute_sql`:

```sql
INSERT INTO storage.buckets (id, name, public)
VALUES ('platts-reports', 'platts-reports', false);
```

- [ ] **Step 3: Verify both exist**

Run `mcp__supabase__list_tables` and `mcp__supabase__execute_sql`:

```sql
SELECT id, name, public FROM storage.buckets WHERE id = 'platts-reports';
```

Expected: table `platts_reports` in list, bucket `platts-reports` with `public=false`.

- [ ] **Step 4: Get project URL and service role key**

Use MCP tools:
- `mcp__claude_ai_Supabase__get_project_url` with id `liqiwvueesohlnnmezyw`
- `mcp__claude_ai_Supabase__get_publishable_keys` with id `liqiwvueesohlnnmezyw`

Note: service role key is in the Supabase dashboard under Settings → API. The publishable key is the anon key. The engineer needs the **service role key** (bypasses RLS). Get it from: `https://supabase.com/dashboard/project/liqiwvueesohlnnmezyw/settings/api`

---

## Task 2: Update package.json — swap deps

**Files:**
- Modify: `actors/platts-scrap-reports/package.json`

- [ ] **Step 1: Update package.json**

Replace the `dependencies` block. Remove `googleapis` and `ioredis`, add `@supabase/supabase-js`:

```json
{
    "name": "platts-scrap-reports",
    "version": "0.2.0",
    "type": "module",
    "description": "Downloads Platts Market + Research Report PDFs to Supabase Storage and Telegram.",
    "dependencies": {
        "apify": "^3.4.2",
        "crawlee": "^3.13.8",
        "playwright": "1.54.1",
        "@supabase/supabase-js": "^2.49.0",
        "node-fetch": "^3.3.2"
    },
    "devDependencies": {
        "@apify/eslint-config": "^1.0.0",
        "eslint": "^9.29.0",
        "eslint-config-prettier": "^10.1.5",
        "prettier": "^3.5.3",
        "vitest": "^2.1.0"
    },
    "scripts": {
        "start": "node src/main.js",
        "format": "prettier --write .",
        "format:check": "prettier --check .",
        "lint": "eslint",
        "lint:fix": "eslint --fix",
        "test": "vitest run",
        "test:watch": "vitest",
        "postinstall": "npx crawlee install-playwright-browsers"
    },
    "license": "ISC"
}
```

- [ ] **Step 2: Install deps**

```bash
cd "/Users/bigode/Dev/Antigravity WF /actors/platts-scrap-reports"
rm -rf node_modules package-lock.json
npm install
```

Expected: installs successfully. `@supabase/supabase-js` present, `googleapis` and `ioredis` gone.

- [ ] **Step 3: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF "
git add actors/platts-scrap-reports/package.json actors/platts-scrap-reports/package-lock.json
git commit -m "chore(reports): swap googleapis+ioredis for @supabase/supabase-js"
```

---

## Task 3: Create supabaseClient.js — singleton client

**Files:**
- Create: `actors/platts-scrap-reports/src/persist/supabaseClient.js`

- [ ] **Step 1: Write supabaseClient.js**

Create `actors/platts-scrap-reports/src/persist/supabaseClient.js`:

```javascript
import { createClient } from '@supabase/supabase-js';
import { log } from 'crawlee';

let client = null;

export function getSupabase() {
    if (client) return client;
    const url = process.env.SUPABASE_URL;
    const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
    if (!url || !key) {
        throw new Error('SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY env vars are required');
    }
    log.info(`Supabase client initialized: ${url}`);
    client = createClient(url, key, {
        auth: { persistSession: false, autoRefreshToken: false },
    });
    return client;
}
```

- [ ] **Step 2: Syntax check + commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF /actors/platts-scrap-reports"
node --check src/persist/supabaseClient.js && echo "OK"
cd "/Users/bigode/Dev/Antigravity WF "
git add actors/platts-scrap-reports/src/persist/supabaseClient.js
git commit -m "feat(reports): add Supabase singleton client"
```

---

## Task 4: Create supabaseUpload.js — storage + metadata insert

**Files:**
- Create: `actors/platts-scrap-reports/src/persist/supabaseUpload.js`

- [ ] **Step 1: Write supabaseUpload.js**

Create `actors/platts-scrap-reports/src/persist/supabaseUpload.js`:

```javascript
import { log } from 'crawlee';

import { getSupabase } from './supabaseClient.js';

const BUCKET = 'platts-reports';

/**
 * Check if a report already exists in the database (dedup).
 * Returns true if the slug+dateKey combo already exists.
 */
export async function isAlreadyStored(slug, dateKey) {
    const sb = getSupabase();
    const { data, error } = await sb
        .from('platts_reports')
        .select('id')
        .eq('slug', slug)
        .eq('date_key', dateKey)
        .limit(1);
    if (error) throw new Error(`Dedup check failed: ${error.message}`);
    return data.length > 0;
}

/**
 * Upload PDF to Supabase Storage and insert metadata into platts_reports.
 * Returns { id, storagePath } on success.
 * Throws on storage or DB error.
 */
export async function uploadPdf(pdfBuffer, { storagePath, metadata }) {
    const sb = getSupabase();

    // 1. Upload to storage bucket
    const { error: storageError } = await sb.storage
        .from(BUCKET)
        .upload(storagePath, pdfBuffer, {
            contentType: 'application/pdf',
            upsert: false,
        });
    if (storageError) {
        throw new Error(`Storage upload failed: ${storageError.message}`);
    }
    log.info(`Stored ${storagePath} in bucket ${BUCKET}`);

    // 2. Insert metadata into platts_reports
    const row = {
        slug: metadata.slug,
        date_key: metadata.dateKey,
        report_name: metadata.reportName,
        report_type: metadata.reportType,
        frequency: metadata.frequency || null,
        cover_date: metadata.coverDate || null,
        published_date: metadata.publishedDate || null,
        storage_path: storagePath,
        file_size_bytes: pdfBuffer.length,
    };
    const { data, error: dbError } = await sb
        .from('platts_reports')
        .insert(row)
        .select('id')
        .single();
    if (dbError) {
        throw new Error(`DB insert failed: ${dbError.message}`);
    }
    log.info(`Inserted platts_reports row: ${data.id}`);
    return { id: data.id, storagePath };
}

/**
 * Update the telegram_message_id for a report after sending the summary.
 */
export async function setTelegramMessageId(reportId, messageId) {
    const sb = getSupabase();
    const { error } = await sb
        .from('platts_reports')
        .update({ telegram_message_id: messageId })
        .eq('id', reportId);
    if (error) {
        log.warning(`Failed to update telegram_message_id for ${reportId}: ${error.message}`);
    }
}
```

- [ ] **Step 2: Syntax check + commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF /actors/platts-scrap-reports"
node --check src/persist/supabaseUpload.js && echo "OK"
cd "/Users/bigode/Dev/Antigravity WF "
git add actors/platts-scrap-reports/src/persist/supabaseUpload.js
git commit -m "feat(reports): add Supabase Storage upload + metadata insert (replaces Drive+Redis)"
```

---

## Task 5: Create telegramSummary.js — summary message with inline buttons

**Files:**
- Create: `actors/platts-scrap-reports/src/notify/telegramSummary.js`

- [ ] **Step 1: Write telegramSummary.js**

Create `actors/platts-scrap-reports/src/notify/telegramSummary.js`:

```javascript
import { log } from 'crawlee';
import fetch from 'node-fetch';

const TG_API = 'https://api.telegram.org/bot';

function escapeMd(s) {
    if (!s) return '';
    return String(s).replace(/([_*`\[\]])/g, '\\$1');
}

/**
 * Build the summary text listing all downloaded reports.
 * @param {string} dateLabel - e.g. "16/04/2026"
 * @param {Array<{reportName, frequency, id}>} reports - downloaded reports with DB id
 */
function buildSummaryText(dateLabel, reports) {
    const lines = reports.map((r) => `• ${escapeMd(r.reportName)} (${escapeMd(r.frequency || '—')})`);
    return (
        `📊 *Platts Reports — ${escapeMd(dateLabel)}*\n\n` +
        `${reports.length} relatório(s) novo(s):\n` +
        lines.join('\n')
    );
}

/**
 * Build inline keyboard: 1 button per report, 1 per row.
 * Callback data: report_dl:<uuid>
 */
function buildKeyboard(reports) {
    return {
        inline_keyboard: reports.map((r) => [
            {
                text: `📥 ${r.reportName}`,
                callback_data: `report_dl:${r.id}`,
            },
        ]),
    };
}

/**
 * Send a single summary message with download buttons.
 * Returns the Telegram message_id (for updating later if needed).
 */
export async function sendReportsSummary(botToken, chatId, dateLabel, reports) {
    if (!botToken) throw new Error('TELEGRAM_BOT_TOKEN required');
    if (!chatId) throw new Error('chatId required');
    if (!reports || reports.length === 0) {
        log.info('No reports to summarize, skipping Telegram.');
        return null;
    }

    const text = buildSummaryText(dateLabel, reports);
    const reply_markup = buildKeyboard(reports);

    const url = `${TG_API}${botToken}/sendMessage`;
    const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            chat_id: String(chatId),
            text,
            parse_mode: 'Markdown',
            reply_markup,
        }),
    });
    if (!res.ok) {
        const body = await res.text();
        throw new Error(`Telegram sendMessage failed: ${res.status} ${body}`);
    }
    const json = await res.json();
    const messageId = json.result?.message_id;
    log.info(`Telegram summary sent (message_id=${messageId}, ${reports.length} buttons)`);
    return messageId;
}
```

- [ ] **Step 2: Syntax check + commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF /actors/platts-scrap-reports"
node --check src/notify/telegramSummary.js && echo "OK"
cd "/Users/bigode/Dev/Antigravity WF "
git add actors/platts-scrap-reports/src/notify/telegramSummary.js
git commit -m "feat(reports): add Telegram summary message with inline download buttons"
```

---

## Task 6: Rewrite main.js — Supabase + summary flow

**Files:**
- Modify: `actors/platts-scrap-reports/src/main.js`

- [ ] **Step 1: Rewrite main.js**

Replace the entire content of `actors/platts-scrap-reports/src/main.js`:

```javascript
import { Actor } from 'apify';
import { log } from 'crawlee';
import { chromium } from 'playwright';

import { loginPlatts } from './auth/login.js';
import { capturePdf } from './download/capturePdf.js';
import { applyExcludeFilter } from './filters/applyFilters.js';
import { extractRows } from './grid/extractRows.js';
import { navigateGrid } from './grid/navigateGrid.js';
import { sendReportsSummary } from './notify/telegramSummary.js';
import { isAlreadyStored, setTelegramMessageId, uploadPdf } from './persist/supabaseUpload.js';
import { datePartsFromIso, parsePublishedDate } from './util/dates.js';
import { slugify } from './util/slug.js';

await Actor.init();

const input = (await Actor.getInput()) ?? {};
const {
    username,
    password,
    reportTypes = ['Market Reports', 'Research Reports'],
    excludeReportNames,
    maxReportsPerType = 50,
    dryRun = false,
    forceRedownload = false,
    telegramChatId,
} = input;

const TG_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const TG_CHAT = telegramChatId || process.env.TELEGRAM_CHAT_ID;
const SB_URL = process.env.SUPABASE_URL;
const SB_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY;

if (!username || !password) {
    await Actor.fail('username and password are required');
}
if (!dryRun && !SB_URL) {
    await Actor.fail('SUPABASE_URL is required when dryRun=false');
}
if (!dryRun && (!TG_TOKEN || !TG_CHAT)) {
    await Actor.fail('TELEGRAM_BOT_TOKEN and chat id required when dryRun=false');
}

const summary = {
    type: 'success',
    reportTypes,
    downloaded: [],
    skipped: [],
    errors: [],
    would_download: [],
};

const browser = await chromium.launch({
    headless: process.env.HEADLESS !== 'false',
    slowMo: process.env.SLOW_MO ? parseInt(process.env.SLOW_MO, 10) : 0,
    channel: 'chrome',
    args: ['--no-sandbox'],
});
const ctx = await browser.newContext({ acceptDownloads: true });
const page = await ctx.newPage();
const pageLog = { info: (m) => log.info(m), warn: (m) => log.warning(m), error: (m) => log.error(m) };

async function run() {
    const loginResult = await loginPlatts(page, username, password, pageLog);
    if (!loginResult.ok) {
        try {
            await page.screenshot({ path: 'login-failure.png', fullPage: true });
            log.warning('Saved login-failure.png');
        } catch (_) { /* ignore */ }
        summary.type = 'error';
        summary.errors.push({ stage: 'login', message: loginResult.reason || 'unknown', detail: loginResult.error });
        await Actor.pushData(summary);
        return;
    }

    // Accumulate downloaded reports across all reportTypes for the summary message
    const allDownloaded = [];

    for (const reportType of reportTypes) {
        try {
            await navigateGrid(page, reportType);
        } catch (e) {
            log.warning(`Skipping "${reportType}": ${e.message}`);
            summary.errors.push({ stage: 'grid-load', reportName: reportType, message: e.message });
            summary.type = 'partial';
            continue;
        }

        const rows = await extractRows(page);
        const filtered = applyExcludeFilter(rows, excludeReportNames).slice(0, maxReportsPerType);
        log.info(`${reportType}: ${rows.length} total, ${filtered.length} after filter (cap ${maxReportsPerType})`);

        for (const row of filtered) {
            const slug = slugify(row.reportName);
            const dateKey = parsePublishedDate(row.coverDate);
            if (!slug || !dateKey) {
                summary.errors.push({ stage: 'parse-row', reportName: row.reportName, message: 'missing slug or dateKey' });
                summary.type = 'partial';
                continue;
            }

            // Dedup via Supabase (or skip in dryRun since we don't connect)
            if (!dryRun && !forceRedownload && (await isAlreadyStored(slug, dateKey))) {
                summary.skipped.push({ slug, dateKey, reason: 'already-exists' });
                continue;
            }

            let pdfBuffer;
            try {
                pdfBuffer = await capturePdf(page, row.rowIndex);
            } catch (e) {
                summary.errors.push({ stage: 'download', reportName: row.reportName, message: e.message });
                summary.type = 'partial';
                continue;
            }

            const parts = datePartsFromIso(dateKey);
            const filename = `${dateKey}_${slug}.pdf`;
            const reportTypeSlug = slugify(reportType);
            const storagePath = `${reportTypeSlug}/${parts.year}/${parts.month}/${filename}`;

            if (dryRun) {
                summary.would_download.push({ slug, dateKey, storagePath, sizeBytes: pdfBuffer.length });
                continue;
            }

            let uploadResult;
            try {
                uploadResult = await uploadPdf(pdfBuffer, {
                    storagePath,
                    metadata: {
                        slug,
                        dateKey,
                        reportName: row.reportName,
                        reportType,
                        frequency: row.frequency,
                        coverDate: row.coverDate,
                        publishedDate: row.publishedDate,
                    },
                });
            } catch (e) {
                log.warning(`Supabase upload failed for ${filename}: ${e.message}`);
                summary.errors.push({ stage: 'supabase-upload', reportName: row.reportName, message: e.message });
                summary.type = 'partial';
                continue;
            }

            allDownloaded.push({
                id: uploadResult.id,
                slug,
                dateKey,
                storagePath,
                reportName: row.reportName,
                frequency: row.frequency,
            });
            summary.downloaded.push({ slug, dateKey, storagePath, supabaseId: uploadResult.id });
        }
    }

    // Send 1 Telegram summary with download buttons (after all report types processed)
    if (!dryRun && allDownloaded.length > 0) {
        const todayLabel = new Date().toLocaleDateString('pt-BR', { timeZone: 'America/Sao_Paulo' });
        try {
            const messageId = await sendReportsSummary(TG_TOKEN, TG_CHAT, todayLabel, allDownloaded);
            // Update telegram_message_id on all downloaded reports
            for (const report of allDownloaded) {
                await setTelegramMessageId(report.id, messageId);
            }
        } catch (e) {
            log.warning(`Telegram summary failed: ${e.message}`);
            summary.errors.push({ stage: 'telegram-summary', message: e.message });
            summary.type = 'partial';
        }
    }

    await Actor.pushData(summary);
}

try {
    await run();
} finally {
    await ctx.close();
    await browser.close();
    await Actor.exit();
}
```

- [ ] **Step 2: Syntax check**

```bash
cd "/Users/bigode/Dev/Antigravity WF /actors/platts-scrap-reports"
node --check src/main.js && echo "OK"
```

- [ ] **Step 3: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF "
git add actors/platts-scrap-reports/src/main.js
git commit -m "feat(reports): rewrite main.js for Supabase + Telegram summary flow"
```

---

## Task 7: Update input_schema.json — remove Drive, update descriptions

**Files:**
- Modify: `actors/platts-scrap-reports/.actor/input_schema.json`

- [ ] **Step 1: Replace input_schema.json**

Overwrite `actors/platts-scrap-reports/.actor/input_schema.json`:

```json
{
    "title": "Platts Reports PDF Downloader",
    "type": "object",
    "schemaVersion": 1,
    "properties": {
        "username": {
            "title": "Platts username",
            "type": "string",
            "editor": "textfield",
            "description": "Platts Connect SSO email/username.",
            "isSecret": true
        },
        "password": {
            "title": "Platts password",
            "type": "string",
            "editor": "textfield",
            "description": "Platts Connect SSO password.",
            "isSecret": true
        },
        "reportTypes": {
            "title": "Report categories to scrape",
            "type": "array",
            "editor": "stringList",
            "default": ["Market Reports", "Research Reports"],
            "description": "Each entry maps to a tab on core.spglobal.com/#platts/rptsSearch."
        },
        "excludeReportNames": {
            "title": "Substrings to exclude (case-insensitive)",
            "type": "array",
            "editor": "stringList",
            "default": [
                "- Portuguese",
                "(Português)",
                "(Portugues)",
                "(Español)",
                "Perspectiva Global",
                "Panorama Semanal"
            ],
            "description": "Skip rows whose Report Name contains any of these substrings. Used to drop translated duplicates."
        },
        "maxReportsPerType": {
            "title": "Max reports per category",
            "type": "integer",
            "description": "Upper bound of rows processed per report-type grid (safety cap).",
            "default": 50,
            "minimum": 1,
            "maximum": 200
        },
        "dryRun": {
            "title": "Dry run (no upload, no Telegram)",
            "type": "boolean",
            "description": "If true, extracts and captures PDFs but skips Supabase upload and Telegram notification.",
            "default": false
        },
        "forceRedownload": {
            "title": "Ignore dedup, re-download everything",
            "type": "boolean",
            "description": "If true, ignores existing records in Supabase and re-processes every row.",
            "default": false
        },
        "telegramChatId": {
            "title": "Telegram chat ID (overrides env)",
            "type": "string",
            "editor": "textfield",
            "description": "Optional. If empty, uses TELEGRAM_CHAT_ID env."
        }
    },
    "required": ["username", "password"]
}
```

- [ ] **Step 2: Validate JSON + commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF "
python3 -c "import json; json.load(open('actors/platts-scrap-reports/.actor/input_schema.json')); print('OK')"
git add actors/platts-scrap-reports/.actor/input_schema.json
git commit -m "chore(reports): remove Drive fields from input schema, update descriptions for Supabase"
```

---

## Task 8: Delete old Drive + Redis modules

**Files:**
- Delete: `actors/platts-scrap-reports/src/persist/gdriveUpload.js`
- Delete: `actors/platts-scrap-reports/src/persist/redisDedup.js`
- Delete: `actors/platts-scrap-reports/src/notify/telegramSend.js` (replaced by telegramSummary.js)

- [ ] **Step 1: Remove files + commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF "
git rm actors/platts-scrap-reports/src/persist/gdriveUpload.js
git rm actors/platts-scrap-reports/src/persist/redisDedup.js
git rm actors/platts-scrap-reports/src/notify/telegramSend.js
git commit -m "chore(reports): remove Drive, Redis dedup, and per-PDF Telegram modules"
```

---

## Task 9: Run tests — verify nothing broke

**Files:** None (verification only)

- [ ] **Step 1: Run existing tests**

```bash
cd "/Users/bigode/Dev/Antigravity WF /actors/platts-scrap-reports"
npm test 2>&1 | tail -15
```

Expected: 19/19 tests pass (slug, dates, filters — all pure utils, unaffected).

- [ ] **Step 2: Full syntax check across all actor JS**

```bash
cd "/Users/bigode/Dev/Antigravity WF /actors/platts-scrap-reports"
for f in $(find src -name "*.js"); do node --check "$f" || echo "FAIL: $f"; done
echo "---"
echo "All syntax checks done"
```

Expected: all files OK. No FAIL lines.

---

## Task 10: Webhook — add `report_dl` callback handler

**Files:**
- Modify: `webhook/app.py` — add handler in `handle_callback()`
- Modify: `webhook/requirements.txt` — add `supabase`

- [ ] **Step 1: Add supabase to webhook requirements**

Append to `webhook/requirements.txt`:

```
supabase>=2.0.0,<3.0
```

- [ ] **Step 2: Add Supabase client init at top of webhook/app.py**

After the existing imports (around line 30), add:

```python
# Supabase client for report downloads
_supabase_client = None

def get_supabase():
    global _supabase_client
    if _supabase_client is None:
        sb_url = os.environ.get("SUPABASE_URL")
        sb_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        if not sb_url or not sb_key:
            logger.warning("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set — report downloads disabled")
            return None
        from supabase import create_client
        _supabase_client = create_client(sb_url, sb_key)
    return _supabase_client
```

- [ ] **Step 3: Add `report_dl` handler in `handle_callback()`**

Inside `handle_callback()` (in `webhook/app.py`), before the existing `elif action == "curate_archive":` block, add:

```python
    # ---------- Report PDF download ----------
    if action == "report_dl":
        report_id = item_id  # UUID from callback data
        sb = get_supabase()
        if not sb:
            answer_callback(callback_id, "Supabase não configurado")
            return jsonify({"ok": True})
        try:
            row = sb.table("platts_reports").select("storage_path, report_name").eq("id", report_id).single().execute()
            if not row.data:
                answer_callback(callback_id, "Relatório não encontrado")
                return jsonify({"ok": True})
            storage_path = row.data["storage_path"]
            report_name = row.data["report_name"]
            signed = sb.storage.from_("platts-reports").create_signed_url(storage_path, 3600)
            if not signed or not signed.get("signedURL"):
                answer_callback(callback_id, "Erro ao gerar link")
                return jsonify({"ok": True})
            pdf_url = signed["signedURL"]
            # Download PDF and send as document
            pdf_resp = requests.get(pdf_url, timeout=30)
            pdf_resp.raise_for_status()
            filename = storage_path.split("/")[-1]
            telegram_api("sendDocument", {
                "chat_id": chat_id,
                "caption": f"📄 {report_name}",
                "parse_mode": "Markdown",
            }, files={"document": (filename, pdf_resp.content, "application/pdf")})
            answer_callback(callback_id, f"📤 {report_name}")
        except Exception as exc:
            logger.error(f"report_dl error: {exc}")
            answer_callback(callback_id, "Erro ao baixar relatório")
        return jsonify({"ok": True})
```

**IMPORTANT:** The `telegram_api` function currently sends JSON. For file uploads it needs `multipart/form-data`. Check if the existing `telegram_api` helper supports `files` parameter. If NOT, use `requests.post` directly:

```python
            # Direct multipart upload (telegram_api may not support files param)
            resp = requests.post(
                f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendDocument",
                data={"chat_id": chat_id, "caption": f"📄 {report_name}", "parse_mode": "Markdown"},
                files={"document": (filename, pdf_resp.content, "application/pdf")},
                timeout=30,
            )
            if not resp.json().get("ok"):
                logger.warning(f"sendDocument failed: {resp.text[:200]}")
```

- [ ] **Step 4: Verify the `telegram_api` helper signature**

Read `webhook/app.py` around line 595-615 to check if `telegram_api` accepts a `files` parameter. If it doesn't, use the `requests.post` direct approach from Step 3 above.

- [ ] **Step 5: Verify the action/item_id parsing**

Check how `handle_callback` extracts `action` and `item_id` from `callback_data`. The callback data format is `report_dl:<uuid>`. Make sure the existing split logic handles this:

```python
# Typical pattern in handle_callback:
parts = callback_data.split(":", 1)
action = parts[0]
item_id = parts[1] if len(parts) > 1 else ""
```

Verify this matches the code. If the existing code uses a different split pattern, adapt the `report_dl` handler.

- [ ] **Step 6: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF "
git add webhook/app.py webhook/requirements.txt
git commit -m "feat(webhook): add report_dl callback for on-demand PDF download via Supabase"
```

---

## Task 11: Update GH Actions workflow env vars

**Files:**
- Modify: `.github/workflows/platts_reports.yml`

- [ ] **Step 1: Update env vars in workflow**

In `.github/workflows/platts_reports.yml`, replace the env block under "Run Platts Reports":

```yaml
        env:
          APIFY_API_TOKEN: ${{ secrets.APIFY_API_TOKEN }}
          APIFY_PLATTS_REPORTS_ACTOR_ID: bigodeio05/platts-scrap-reports
          PLATTS_USERNAME: ${{ secrets.PLATTS_USERNAME }}
          PLATTS_PASSWORD: ${{ secrets.PLATTS_PASSWORD }}
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          REDIS_URL: ${{ secrets.REDIS_URL }}
```

Note: `REDIS_URL` kept because the Python wrapper `platts_reports.py` uses `state_store` which writes to Redis. Only the ACTOR doesn't need Redis anymore. The GH workflow still passes it for the Python wrapper. `GDRIVE_PLATTS_REPORTS_FOLDER_ID`, `GOOGLE_CREDENTIALS_JSON` removed.

- [ ] **Step 2: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF "
git add .github/workflows/platts_reports.yml
git commit -m "chore(ci): update platts_reports env vars for Supabase (remove Drive/Google)"
```

---

## Task 12: Set env vars on Apify actor + Railway + GitHub

**Files:** None (infra config)

- [ ] **Step 1: Get Supabase project URL and service role key**

Project URL: `https://liqiwvueesohlnnmezyw.supabase.co`

Service role key: Go to https://supabase.com/dashboard/project/liqiwvueesohlnnmezyw/settings/api → copy "service_role" key (the long `eyJ...` JWT).

- [ ] **Step 2: Set env vars on Apify actor**

Console Apify → actor → Source → Environment Variables:
- Add `SUPABASE_URL` = `https://liqiwvueesohlnnmezyw.supabase.co`
- Add `SUPABASE_SERVICE_ROLE_KEY` = `<service role key>`
- Remove: `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REFRESH_TOKEN`, `GOOGLE_CREDENTIALS_JSON`, `REDIS_URL`
- Keep: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`

- [ ] **Step 3: Set env vars on Railway (webhook)**

Railway dashboard → project → webhook service → Variables:
- Add `SUPABASE_URL` = `https://liqiwvueesohlnnmezyw.supabase.co`
- Add `SUPABASE_SERVICE_ROLE_KEY` = `<same service role key>`

- [ ] **Step 4: Add GitHub secrets**

Repo → Settings → Secrets and variables → Actions:
- Add `SUPABASE_URL` = `https://liqiwvueesohlnnmezyw.supabase.co`
- Add `SUPABASE_SERVICE_ROLE_KEY` = `<same service role key>`
- Remove: `GDRIVE_PLATTS_REPORTS_FOLDER_ID`

---

## Task 13: Push actor + end-to-end test

- [ ] **Step 1: Push actor to Apify**

```bash
cd "/Users/bigode/Dev/Antigravity WF /actors/platts-scrap-reports"
apify push --force
```

- [ ] **Step 2: dryRun test**

Run with `dryRun=true`, `maxReportsPerType=3`, `reportTypes=["Market Reports"]`.

Expected: `would_download[]` with 3 items, no errors. Supabase not touched.

- [ ] **Step 3: Full test (1 report)**

Run with `dryRun=false`, `maxReportsPerType=1`, `reportTypes=["Market Reports"]`.

Expected:
- 1 PDF in Supabase Storage at `market-reports/2026/04/<date>_<slug>.pdf`
- 1 row in `platts_reports` table with all metadata filled
- 1 Telegram summary message with 1 inline button
- Clicking the button → webhook sends the PDF as document

- [ ] **Step 4: Verify dedup**

Re-run same input. Expected: `skipped: [{reason: "already-exists"}]`, zero downloads.

- [ ] **Step 5: Full run (all reports)**

Run with `dryRun=false`, `maxReportsPerType=50`, `reportTypes=["Market Reports", "Research Reports"]`.

Verify: all English reports downloaded, stored, summarized.

- [ ] **Step 6: Push code to GitHub**

```bash
cd "/Users/bigode/Dev/Antigravity WF "
git push origin main
```

---

## Self-Review

**Spec coverage:**
- Supabase bucket + table → Task 1 ✓
- supabaseUpload replacing gdriveUpload → Tasks 3-4 ✓
- main.js rewrite → Task 6 ✓
- Dedup via Postgres UNIQUE → Task 4 (isAlreadyStored) + Task 6 (flow) ✓
- Telegram summary + buttons → Task 5 ✓
- Webhook report_dl callback → Task 10 ✓
- Remove Drive/Redis modules → Task 8 ✓
- Swap npm deps → Task 2 ✓
- Input schema update → Task 7 ✓
- Env vars (Apify/Railway/GitHub) → Tasks 11-12 ✓
- Error handling → Task 6 (try/catch per stage) + Task 10 (webhook error handling) ✓
- Success criteria verification → Task 13 ✓

**Placeholder scan:** No TBD/TODO. Task 10 Step 4 says "verify telegram_api signature" which is a conditional check, not a placeholder. Code shown for both branches.

**Type consistency:**
- `uploadPdf({storagePath, metadata})` in Task 4 matches call in Task 6
- `isAlreadyStored(slug, dateKey)` in Task 4 matches call in Task 6
- `sendReportsSummary(botToken, chatId, dateLabel, reports)` in Task 5 matches call in Task 6
- `report_dl:<uuid>` callback data format in Task 5 (buildKeyboard) matches Task 10 (handler)
- `setTelegramMessageId(reportId, messageId)` in Task 4 matches call in Task 6
