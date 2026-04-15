# Platts Reports PDF Downloader — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Apify-specific skills to invoke during execution:**
> - `apify-actor-development` — when scaffolding actor + writing Apify SDK code
> - `apify-generate-output-schema` — when defining `dataset_schema.json`
>
> **Spec reference:** `docs/superpowers/specs/2026-04-15-platts-reports-actor-design.md`

**Goal:** Build a new Apify Actor (`platts-scrap-reports`) that logs into Platts Connect, scrapes Market + Research Reports grids, downloads new PDFs, archives them in Google Drive, and sends them to Telegram.

**Architecture:** Playwright-based actor reusing the existing Okta login flow from `platts-scrap-full-news`. Sequential per-row processing (Akamai-friendly). Dedup via Redis (`platts:report:seen:<slug>:<date>`, TTL 90d). Drive upload via existing service account (`n8n-397@n8nminerals.iam.gserviceaccount.com`). Telegram delivery via existing `TelegramClient` and chat ID. Wrapped by Python `platts_reports.py` that triggers the actor and records workflow state. Scheduled daily via GitHub Actions.

**Tech Stack:** Apify SDK 3, Crawlee 3, Playwright 1.54, Node 20 (ESM), googleapis (Drive v3), ioredis, Vitest (utils only). Wrapper: Python 3.10, dotenv, redis-py.

---

## File Structure (locked decisions)

**New files (actor):**
- `actors/platts-scrap-reports/package.json`
- `actors/platts-scrap-reports/Dockerfile` (extends `apify/actor-node-playwright-chrome`)
- `actors/platts-scrap-reports/.actor/actor.json`
- `actors/platts-scrap-reports/.actor/input_schema.json`
- `actors/platts-scrap-reports/.actor/output_schema.json`
- `actors/platts-scrap-reports/.actor/dataset_schema.json`
- `actors/platts-scrap-reports/src/main.js` — orchestration
- `actors/platts-scrap-reports/src/auth/login.js` — copied from news actor
- `actors/platts-scrap-reports/src/grid/navigateGrid.js`
- `actors/platts-scrap-reports/src/grid/extractRows.js`
- `actors/platts-scrap-reports/src/filters/applyFilters.js`
- `actors/platts-scrap-reports/src/download/capturePdf.js`
- `actors/platts-scrap-reports/src/storage/gdriveUpload.js`
- `actors/platts-scrap-reports/src/storage/redisDedup.js`
- `actors/platts-scrap-reports/src/notify/telegramSend.js`
- `actors/platts-scrap-reports/src/util/slug.js`
- `actors/platts-scrap-reports/src/util/dates.js`
- `actors/platts-scrap-reports/tests/slug.test.js`
- `actors/platts-scrap-reports/tests/dates.test.js`
- `actors/platts-scrap-reports/tests/filters.test.js`

**New files (Python wrapper + workflow):**
- `execution/scripts/platts_reports.py`
- `.github/workflows/platts_reports.yml`

**Modified files:**
- `.env` — add `GDRIVE_PLATTS_REPORTS_FOLDER_ID=1KxixMP9rKF0vGzINGvmmyFvouaOvL02y` + (optional) `APIFY_PLATTS_REPORTS_ACTOR_ID=bigodeio05/platts-scrap-reports`
- `requirements.txt` — already has all needed deps (apify-client, redis, python-dotenv, google-auth, googleapiclient if used; check during impl)

**Out of TDD scope (manual integration test only):** Playwright code (login, grid nav, extract, capture), Drive API calls, Telegram API calls. Manual test = `dryRun: true` against real portal.

**In TDD scope:** `slug.js`, `dates.js`, `applyFilters.js` (pure functions).

---

## Phase 1: Investigation Spike

### Task 1: Live portal inspection (manual, headed Playwright)

**Goal:** Resolve the 6 open items from the spec by inspecting the live portal in a headed browser. Output: a markdown notes file with concrete selectors and download mechanism.

**Files:**
- Create: `actors/.investigation/reports-spike.md` (gitignored, but useful artifact)
- Create: `actors/.investigation/reports-spike.js` (gitignored throwaway script)

- [ ] **Step 1: Write a headed Playwright spike script**

Create `actors/.investigation/reports-spike.js`:

```javascript
// Throwaway spike — run with: node actors/.investigation/reports-spike.js
import 'dotenv/config';
import { chromium } from 'playwright';
import { loginPlatts } from '../platts-scrap-full-news/src/auth/login.js';

const { PLATTS_USERNAME, PLATTS_PASSWORD } = process.env;
if (!PLATTS_USERNAME || !PLATTS_PASSWORD) {
    console.error('Set PLATTS_USERNAME and PLATTS_PASSWORD in .env');
    process.exit(1);
}

const browser = await chromium.launch({ headless: false, slowMo: 200 });
const ctx = await browser.newContext({ acceptDownloads: true });
const page = await ctx.newPage();

const pageLog = { info: console.log, warn: console.warn, error: console.error };
const result = await loginPlatts(page, PLATTS_USERNAME, PLATTS_PASSWORD, pageLog);
if (!result.ok) {
    console.error('LOGIN FAILED:', result);
    process.exit(2);
}
console.log('Login OK. Navigating to Market Reports...');

await page.goto('https://core.spglobal.com/#platts/rptsSearch?reportType=Market%20Reports', { waitUntil: 'networkidle' });
console.log('At grid. Open DevTools, inspect:');
console.log('  - Selector of the table (try table.report-grid, [role="grid"], etc.)');
console.log('  - Selector of each row + columns (Report Name, Frequency, Cover Date, Published Date, Actions)');
console.log('  - Selector of the PDF icon in Actions column (aria-label, title, class)');
console.log('  - What happens on click: download? new tab? popup?');
console.log('Press Ctrl+C to exit.');

// Keep open for manual exploration
await new Promise(() => {});
```

- [ ] **Step 2: Run the spike and document findings**

Run: `node actors/.investigation/reports-spike.js`

Manually inspect the page. Fill in `actors/.investigation/reports-spike.md` with answers to each open item:

```markdown
# Reports Spike Findings — 2026-04-15

## 1. Grid table selector
- Selector: `<exact CSS or XPath>`
- Contains how many rows by default? <number>

## 2. Row + column selectors
- Row: `<selector>`
- Report Name cell: `<selector>` (text() or .innerText)
- Frequency cell: `<selector>`
- Cover Date cell: `<selector>`
- Published Date cell: `<selector>` (full timestamp visible)
- Actions cell: `<selector>`

## 3. PDF action icon selector
- Selector: `<selector>` (likely `[aria-label="Download PDF"]` or `.pdf-icon` or 3rd `<a>` in Actions)
- Behavior on click:
  - [ ] Triggers download event (best case → use `page.waitForEvent('download')`)
  - [ ] Opens new tab with PDF viewer (fallback → intercept response)
  - [ ] Calls API returning signed URL (fallback → fetch URL inside Playwright context)

## 4. Pagination
- Total reports per type (visible after scrolling/clicking next): <number>
- Mechanism: [ ] infinite scroll  [ ] page numbers  [ ] no pagination (all loaded)

## 5. Grid load time after navigation
- Approximate ms from navigation to first row visible: <ms>
- Selector that signals "ready": `<selector>` (e.g., `tbody tr:first-child`)

## 6. Research Reports — same structure?
- Visit https://core.spglobal.com/#platts/rptsSearch?reportType=Research%20Reports
- Same selectors work? [ ] yes  [ ] no — differences: <list>

## 7. Sample published date format observed
- Examples: `15/04/2026 10:24:16 UTC`, `<other formats?>`
```

- [ ] **Step 3: Commit the spike notes (only the .md, not the .js)**

```bash
# .gitignore already excludes actors/.investigation/ — temporarily allow only the .md
git add -f actors/.investigation/reports-spike.md
git commit -m "docs: live portal spike findings for Platts Reports actor"
```

**Note:** The `reports-spike.js` stays out of git. Findings in `.md` are referenced by subsequent tasks. **All later tasks assume Step 2 produced concrete answers**; if any open item remains "unknown" after the spike, pause and re-investigate before continuing.

---

## Phase 2: Scaffold the actor

### Task 2: Copy actor skeleton from news actor

**Files:**
- Create: `actors/platts-scrap-reports/` (entire dir)

- [ ] **Step 1: Copy news actor and rename**

```bash
cd "/Users/bigode/Dev/Antigravity WF "
cp -R actors/platts-scrap-full-news actors/platts-scrap-reports
# Remove copied source files we'll rewrite
rm -rf actors/platts-scrap-reports/src/sources
rm -rf actors/platts-scrap-reports/src/extract
rm -f actors/platts-scrap-reports/src/main.js
rm -rf actors/platts-scrap-reports/src/util
mkdir -p actors/platts-scrap-reports/src/{grid,filters,download,storage,notify,util,tests}
mkdir -p actors/platts-scrap-reports/tests
```

- [ ] **Step 2: Update package.json**

Replace `actors/platts-scrap-reports/package.json` with:

```json
{
    "name": "platts-scrap-reports",
    "version": "0.1.0",
    "type": "module",
    "description": "Downloads Platts Market + Research Report PDFs to Google Drive and Telegram.",
    "dependencies": {
        "apify": "^3.4.2",
        "crawlee": "^3.13.8",
        "playwright": "1.54.1",
        "googleapis": "^144.0.0",
        "ioredis": "^5.4.1",
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

- [ ] **Step 3: Update .actor/actor.json**

Replace `actors/platts-scrap-reports/.actor/actor.json` with:

```json
{
    "actorSpecification": 1,
    "name": "platts-scrap-reports",
    "title": "Platts Reports PDF Downloader",
    "description": "Downloads Market + Research Report PDFs to Google Drive and Telegram.",
    "version": "0.1",
    "input": "./input_schema.json",
    "output": "./output_schema.json",
    "storages": {
        "dataset": "./dataset_schema.json"
    },
    "dockerfile": "../Dockerfile"
}
```

- [ ] **Step 4: Verify auth/login.js was preserved**

Run: `ls actors/platts-scrap-reports/src/auth/login.js && head -20 actors/platts-scrap-reports/src/auth/login.js`

Expected: file exists, contains `LOGIN_URL = 'https://core.spglobal.com/web/index1.html#login'`.

- [ ] **Step 5: Install deps**

```bash
cd "/Users/bigode/Dev/Antigravity WF /actors/platts-scrap-reports"
npm install
```

Expected: success, no peer-dep warnings of concern.

- [ ] **Step 6: Commit scaffold**

```bash
cd "/Users/bigode/Dev/Antigravity WF "
git add actors/platts-scrap-reports/
git commit -m "feat(actors): scaffold platts-scrap-reports actor skeleton"
```

---

## Phase 3: Pure utils with TDD

### Task 3: slug.js — slugify report names

**Files:**
- Create: `actors/platts-scrap-reports/src/util/slug.js`
- Test: `actors/platts-scrap-reports/tests/slug.test.js`

- [ ] **Step 1: Write the failing test**

Create `actors/platts-scrap-reports/tests/slug.test.js`:

```javascript
import { describe, it, expect } from 'vitest';
import { slugify } from '../src/util/slug.js';

describe('slugify', () => {
    it('lowercases and replaces spaces with hyphens', () => {
        expect(slugify('SBB Steel Markets Daily')).toBe('sbb-steel-markets-daily');
    });

    it('strips non-alphanumeric except hyphens', () => {
        expect(slugify('Global Market Outlook (Português)')).toBe('global-market-outlook-portugues');
    });

    it('collapses multiple spaces and special chars to single hyphens', () => {
        expect(slugify('  Steel  &  Iron — Daily  ')).toBe('steel-iron-daily');
    });

    it('removes leading/trailing hyphens', () => {
        expect(slugify('--Hello--')).toBe('hello');
    });

    it('handles empty string', () => {
        expect(slugify('')).toBe('');
    });

    it('handles accents (basic ASCII fold)', () => {
        expect(slugify('Análise Diária')).toBe('analise-diaria');
    });
});
```

- [ ] **Step 2: Run test, verify failure**

Run: `cd actors/platts-scrap-reports && npm test -- slug`

Expected: FAIL, "Cannot find module '../src/util/slug.js'" or similar.

- [ ] **Step 3: Implement slugify**

Create `actors/platts-scrap-reports/src/util/slug.js`:

```javascript
/**
 * Convert a report name to a URL/filename-safe slug.
 *
 *   "SBB Steel Markets Daily" → "sbb-steel-markets-daily"
 *   "Global Market Outlook (Português)" → "global-market-outlook-portugues"
 */
export function slugify(input) {
    if (!input || typeof input !== 'string') return '';
    return input
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '')
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '');
}
```

- [ ] **Step 4: Run test, verify pass**

Run: `cd actors/platts-scrap-reports && npm test -- slug`

Expected: 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF "
git add actors/platts-scrap-reports/src/util/slug.js actors/platts-scrap-reports/tests/slug.test.js
git commit -m "feat(reports): add slugify util with tests"
```

---

### Task 4: dates.js — parse Platts published-date strings

**Files:**
- Create: `actors/platts-scrap-reports/src/util/dates.js`
- Test: `actors/platts-scrap-reports/tests/dates.test.js`

- [ ] **Step 1: Write the failing test**

Create `actors/platts-scrap-reports/tests/dates.test.js`:

```javascript
import { describe, it, expect } from 'vitest';
import { parsePublishedDate, datePartsFromIso } from '../src/util/dates.js';

describe('parsePublishedDate', () => {
    it('parses "DD/MM/YYYY HH:MM:SS UTC" → ISO date string', () => {
        expect(parsePublishedDate('15/04/2026 10:24:16 UTC')).toBe('2026-04-15');
    });

    it('parses "DD/MM/YYYY" alone', () => {
        expect(parsePublishedDate('14/04/2026')).toBe('2026-04-14');
    });

    it('parses Portuguese short month "15 abr. 2026"', () => {
        expect(parsePublishedDate('15 abr. 2026')).toBe('2026-04-15');
    });

    it('parses English short month "15 Apr 2026"', () => {
        expect(parsePublishedDate('15 Apr 2026')).toBe('2026-04-15');
    });

    it('returns null for unparseable input', () => {
        expect(parsePublishedDate('not a date')).toBeNull();
        expect(parsePublishedDate('')).toBeNull();
        expect(parsePublishedDate(null)).toBeNull();
    });
});

describe('datePartsFromIso', () => {
    it('splits "2026-04-15" → { year, month, day }', () => {
        expect(datePartsFromIso('2026-04-15')).toEqual({ year: '2026', month: '04', day: '15' });
    });
});
```

- [ ] **Step 2: Run test, verify failure**

Run: `cd actors/platts-scrap-reports && npm test -- dates`

Expected: FAIL.

- [ ] **Step 3: Implement parsers**

Create `actors/platts-scrap-reports/src/util/dates.js`:

```javascript
const PT_MONTHS = {
    'jan': '01', 'fev': '02', 'mar': '03', 'abr': '04', 'mai': '05', 'jun': '06',
    'jul': '07', 'ago': '08', 'set': '09', 'out': '10', 'nov': '11', 'dez': '12',
};
const EN_MONTHS = {
    'jan': '01', 'feb': '02', 'mar': '03', 'apr': '04', 'may': '05', 'jun': '06',
    'jul': '07', 'aug': '08', 'sep': '09', 'oct': '10', 'nov': '11', 'dec': '12',
};

export function parsePublishedDate(raw) {
    if (!raw || typeof raw !== 'string') return null;
    const s = raw.trim();

    // "DD/MM/YYYY" with optional time/UTC suffix
    const slash = s.match(/^(\d{2})\/(\d{2})\/(\d{4})(?:\s|$)/);
    if (slash) return `${slash[3]}-${slash[2]}-${slash[1]}`;

    // "DD <month-abbrev>. YYYY" or "DD <month-abbrev> YYYY"
    const word = s.match(/^(\d{1,2})\s+([a-z]{3})\.?\s+(\d{4})/i);
    if (word) {
        const day = word[1].padStart(2, '0');
        const monAbbr = word[2].toLowerCase();
        const month = PT_MONTHS[monAbbr] || EN_MONTHS[monAbbr];
        if (month) return `${word[3]}-${month}-${day}`;
    }

    return null;
}

export function datePartsFromIso(iso) {
    if (!iso || typeof iso !== 'string') return null;
    const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!m) return null;
    return { year: m[1], month: m[2], day: m[3] };
}
```

- [ ] **Step 4: Run test, verify pass**

Run: `cd actors/platts-scrap-reports && npm test -- dates`

Expected: 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF "
git add actors/platts-scrap-reports/src/util/dates.js actors/platts-scrap-reports/tests/dates.test.js
git commit -m "feat(reports): add date parsers (PT/EN months + slash format)"
```

---

### Task 5: applyFilters.js — exclude translated duplicates

**Files:**
- Create: `actors/platts-scrap-reports/src/filters/applyFilters.js`
- Test: `actors/platts-scrap-reports/tests/filters.test.js`

- [ ] **Step 1: Write the failing test**

Create `actors/platts-scrap-reports/tests/filters.test.js`:

```javascript
import { describe, it, expect } from 'vitest';
import { applyExcludeFilter, DEFAULT_EXCLUDES } from '../src/filters/applyFilters.js';

const sample = [
    { reportName: 'World Steel Review' },
    { reportName: 'World Steel Review - Portuguese' },
    { reportName: 'Stahl Global' },
    { reportName: 'Panorama Semanal' },
    { reportName: 'Steel Price Report' },
    { reportName: 'Global Market Outlook' },
    { reportName: 'Perspectiva Global del Mercado' },
    { reportName: 'Global Market Outlook (Português)' },
];

describe('applyExcludeFilter', () => {
    it('keeps English originals and removes default-excluded translations', () => {
        const kept = applyExcludeFilter(sample, DEFAULT_EXCLUDES).map((r) => r.reportName);
        expect(kept).toEqual([
            'World Steel Review',
            'Stahl Global',
            'Steel Price Report',
            'Global Market Outlook',
        ]);
    });

    it('case-insensitive substring match', () => {
        const out = applyExcludeFilter([{ reportName: 'WORLD STEEL REVIEW - PORTUGUESE' }], ['- portuguese']);
        expect(out).toEqual([]);
    });

    it('empty exclude list returns all rows', () => {
        const out = applyExcludeFilter(sample, []);
        expect(out.length).toBe(sample.length);
    });

    it('no rows returns empty', () => {
        expect(applyExcludeFilter([], DEFAULT_EXCLUDES)).toEqual([]);
    });
});
```

- [ ] **Step 2: Run test, verify failure**

Run: `cd actors/platts-scrap-reports && npm test -- filters`

Expected: FAIL.

- [ ] **Step 3: Implement filter**

Create `actors/platts-scrap-reports/src/filters/applyFilters.js`:

```javascript
export const DEFAULT_EXCLUDES = [
    '- Portuguese',
    '(Português)',
    '(Portugues)',
    '(Español)',
    'Perspectiva Global',
    'Panorama Semanal',
];

/**
 * Filter rows whose reportName contains any excluded substring (case-insensitive).
 * @param {Array<{reportName: string}>} rows
 * @param {string[]} excludes
 */
export function applyExcludeFilter(rows, excludes) {
    if (!Array.isArray(rows) || rows.length === 0) return [];
    if (!Array.isArray(excludes) || excludes.length === 0) return rows.slice();
    const lowerExcludes = excludes.map((s) => s.toLowerCase());
    return rows.filter((row) => {
        const name = (row.reportName || '').toLowerCase();
        return !lowerExcludes.some((pat) => name.includes(pat));
    });
}
```

- [ ] **Step 4: Run test, verify pass**

Run: `cd actors/platts-scrap-reports && npm test -- filters`

Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF "
git add actors/platts-scrap-reports/src/filters/applyFilters.js actors/platts-scrap-reports/tests/filters.test.js
git commit -m "feat(reports): add exclude filter for translated duplicates"
```

---

## Phase 4: Apify-side modules (no TDD; manual integration testing)

### Task 6: input_schema.json + output_schema.json + dataset_schema.json

**Files:**
- Create: `actors/platts-scrap-reports/.actor/input_schema.json`
- Create: `actors/platts-scrap-reports/.actor/output_schema.json`
- Create: `actors/platts-scrap-reports/.actor/dataset_schema.json`

- [ ] **Step 1: Invoke `apify-actor-development` skill**

Read the skill briefly to refresh the canonical schema patterns.

- [ ] **Step 2: Write input_schema.json**

Create `actors/platts-scrap-reports/.actor/input_schema.json`:

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
            "isSecret": true
        },
        "password": {
            "title": "Platts password",
            "type": "string",
            "editor": "textfield",
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
            "default": 50,
            "minimum": 1,
            "maximum": 200
        },
        "dryRun": {
            "title": "Dry run (no Drive upload, no Telegram)",
            "type": "boolean",
            "default": false
        },
        "forceRedownload": {
            "title": "Ignore Redis dedup, re-download everything",
            "type": "boolean",
            "default": false
        },
        "gdriveFolderId": {
            "title": "Google Drive root folder ID",
            "type": "string",
            "editor": "textfield",
            "default": "1KxixMP9rKF0vGzINGvmmyFvouaOvL02y",
            "description": "ID of the 'Platts Reports' folder shared with the service account."
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

- [ ] **Step 3: Write output_schema.json**

Create `actors/platts-scrap-reports/.actor/output_schema.json`:

```json
{
    "actorSpecification": 1,
    "fields": {},
    "views": {
        "summary": {
            "title": "Run summary",
            "transformation": {
                "fields": ["type", "downloaded", "skipped", "errors", "would_download"]
            },
            "display": { "component": "table" }
        }
    }
}
```

- [ ] **Step 4: Write dataset_schema.json**

Create `actors/platts-scrap-reports/.actor/dataset_schema.json`:

```json
{
    "actorSpecification": 1,
    "fields": {
        "type": { "type": "string", "description": "success | partial | error" },
        "reportTypes": { "type": "array" },
        "downloaded": { "type": "array", "description": "[{slug, dateKey, drivePath, driveFileId}]" },
        "skipped": { "type": "array", "description": "[{slug, dateKey, reason}]" },
        "errors": { "type": "array", "description": "[{stage, reportName, message}]" },
        "would_download": { "type": "array", "description": "Populated only when dryRun=true" }
    },
    "views": {}
}
```

- [ ] **Step 5: Commit**

```bash
git add actors/platts-scrap-reports/.actor/
git commit -m "feat(reports): define input/output/dataset schemas"
```

---

### Task 7: grid/navigateGrid.js — load a report-type page

**Files:**
- Create: `actors/platts-scrap-reports/src/grid/navigateGrid.js`

**Pre-req:** Task 1 spike notes — needs the "ready" selector from finding #5.

- [ ] **Step 1: Write navigateGrid**

Create `actors/platts-scrap-reports/src/grid/navigateGrid.js`. **Replace `<GRID_READY_SELECTOR>` with the selector from `actors/.investigation/reports-spike.md` finding #5.**

```javascript
import { log } from 'crawlee';

const BASE_URL = 'https://core.spglobal.com/#platts/rptsSearch';
// From spike: selector that signals the grid finished rendering its first row
const GRID_READY_SELECTOR = '<GRID_READY_SELECTOR>';

export async function navigateGrid(page, reportType) {
    const url = `${BASE_URL}?reportType=${encodeURIComponent(reportType)}`;
    log.info(`📄 Navigating to ${reportType} grid: ${url}`);
    await page.goto(url, { waitUntil: 'networkidle', timeout: 60000 });
    try {
        await page.waitForSelector(GRID_READY_SELECTOR, { timeout: 30000 });
    } catch (e) {
        throw new Error(`Grid did not render for "${reportType}" within 30s: ${e.message}`);
    }
    log.info(`✅ Grid ready for ${reportType}`);
}
```

- [ ] **Step 2: Commit**

```bash
git add actors/platts-scrap-reports/src/grid/navigateGrid.js
git commit -m "feat(reports): add grid navigation with ready-selector wait"
```

---

### Task 8: grid/extractRows.js — parse table rows into metadata

**Files:**
- Create: `actors/platts-scrap-reports/src/grid/extractRows.js`

**Pre-req:** spike notes findings #1 and #2.

- [ ] **Step 1: Write extractRows**

Create `actors/platts-scrap-reports/src/grid/extractRows.js`. **Replace each `<...>` selector with values from `actors/.investigation/reports-spike.md`.**

```javascript
import { log } from 'crawlee';

/**
 * Extract metadata for every row in the currently-displayed grid.
 * Returns objects with { reportName, reportTitle, frequency, coverDate, publishedDate, rowIndex }.
 * `rowIndex` lets the download step re-locate the row in the DOM.
 */
export async function extractRows(page) {
    const rows = await page.$$eval('<ROW_SELECTOR>', (rowEls) => {
        return rowEls.map((row, idx) => {
            const cellText = (sel) => {
                const el = row.querySelector(sel);
                return el ? el.innerText.trim() : '';
            };
            return {
                rowIndex: idx,
                reportName: cellText('<REPORT_NAME_CELL>'),
                reportTitle: cellText('<REPORT_TITLE_CELL>'),
                frequency: cellText('<FREQUENCY_CELL>'),
                coverDate: cellText('<COVER_DATE_CELL>'),
                publishedDate: cellText('<PUBLISHED_DATE_CELL>'),
            };
        });
    });
    log.info(`📋 Extracted ${rows.length} rows`);
    return rows.filter((r) => r.reportName); // drop empty
}
```

- [ ] **Step 2: Commit**

```bash
git add actors/platts-scrap-reports/src/grid/extractRows.js
git commit -m "feat(reports): extract grid rows with per-row metadata"
```

---

### Task 9: download/capturePdf.js — click PDF action and capture binary

**Files:**
- Create: `actors/platts-scrap-reports/src/download/capturePdf.js`

**Pre-req:** spike notes finding #3 — determines which branch to implement.

- [ ] **Step 1: Implement the branch matching spike finding**

Create `actors/platts-scrap-reports/src/download/capturePdf.js`. **Pick ONE branch based on what finding #3 said. Delete the other comment block; do not ship dead code.**

```javascript
import { log } from 'crawlee';
import { promises as fs } from 'node:fs';

const PDF_ICON_SELECTOR_TEMPLATE = '<ROW_SELECTOR>:nth-child({n}) <PDF_ICON_SELECTOR>';

/**
 * Click the PDF icon on the row at `rowIndex` and return the downloaded PDF as a Buffer.
 * Throws on timeout, empty download, or non-PDF content type.
 */
export async function capturePdf(page, rowIndex, timeoutMs = 60000) {
    const selector = PDF_ICON_SELECTOR_TEMPLATE.replace('{n}', rowIndex + 1);

    // BRANCH A — direct download event (preferred per spike if behavior=download)
    const downloadPromise = page.waitForEvent('download', { timeout: timeoutMs });
    await page.click(selector);
    const download = await downloadPromise;
    const path = await download.path();
    if (!path) throw new Error('Download saved with no local path');
    const buf = await fs.readFile(path);
    if (buf.length === 0) throw new Error('Downloaded PDF is empty (0 bytes)');
    if (buf.subarray(0, 4).toString('utf-8') !== '%PDF') {
        throw new Error(`Downloaded file is not a PDF (header: ${buf.subarray(0, 4).toString('hex')})`);
    }
    log.info(`📥 Captured PDF (${buf.length} bytes)`);
    return buf;

    // BRANCH B — new tab/popup with viewer (use only if spike said so)
    // const [popup] = await Promise.all([
    //     page.context().waitForEvent('page', { timeout: timeoutMs }),
    //     page.click(selector),
    // ]);
    // const response = await popup.waitForResponse((r) => r.headers()['content-type']?.includes('application/pdf'), { timeout: timeoutMs });
    // const buf = await response.body();
    // await popup.close();
    // return buf;

    // BRANCH C — API returns signed URL (spike branch)
    // const [response] = await Promise.all([
    //     page.waitForResponse((r) => r.url().includes('/api/reports/') && r.request().method() === 'GET'),
    //     page.click(selector),
    // ]);
    // const { downloadUrl } = await response.json();
    // const pdfResp = await page.context().request.get(downloadUrl);
    // return Buffer.from(await pdfResp.body());
}
```

- [ ] **Step 2: Commit**

```bash
git add actors/platts-scrap-reports/src/download/capturePdf.js
git commit -m "feat(reports): capture PDF binary from grid row click"
```

---

### Task 10: storage/redisDedup.js — seen-key checks

**Files:**
- Create: `actors/platts-scrap-reports/src/storage/redisDedup.js`

- [ ] **Step 1: Write Redis client + helpers**

Create `actors/platts-scrap-reports/src/storage/redisDedup.js`:

```javascript
import Redis from 'ioredis';
import { log } from 'crawlee';

const SEEN_TTL_SECONDS = 90 * 24 * 60 * 60; // 90 days

let client = null;

function getClient() {
    if (client) return client;
    const url = process.env.REDIS_URL;
    if (!url) throw new Error('REDIS_URL env var is required for dedup');
    client = new Redis(url, {
        connectTimeout: 5000,
        commandTimeout: 5000,
        maxRetriesPerRequest: 1,
    });
    client.on('error', (err) => log.warning(`Redis error: ${err.message}`));
    return client;
}

export function seenKey(slug, dateKey) {
    return `platts:report:seen:${slug}:${dateKey}`;
}

export async function isSeen(slug, dateKey) {
    const r = getClient();
    const exists = await r.exists(seenKey(slug, dateKey));
    return exists === 1;
}

export async function markSeen(slug, dateKey) {
    const r = getClient();
    await r.set(seenKey(slug, dateKey), '1', 'EX', SEEN_TTL_SECONDS);
}

export async function closeRedis() {
    if (client) {
        await client.quit();
        client = null;
    }
}
```

- [ ] **Step 2: Commit**

```bash
git add actors/platts-scrap-reports/src/storage/redisDedup.js
git commit -m "feat(reports): add Redis dedup with platts:report:seen keyspace (90d TTL)"
```

---

### Task 11: storage/gdriveUpload.js — service-account upload + folder hierarchy

**Files:**
- Create: `actors/platts-scrap-reports/src/storage/gdriveUpload.js`

- [ ] **Step 1: Implement Drive client + upload**

Create `actors/platts-scrap-reports/src/storage/gdriveUpload.js`:

```javascript
import { google } from 'googleapis';
import { log } from 'crawlee';
import { Readable } from 'node:stream';

const FOLDER_MIME = 'application/vnd.google-apps.folder';

let driveClient = null;
const folderCache = new Map(); // path → folderId

function getDrive() {
    if (driveClient) return driveClient;
    const raw = process.env.GOOGLE_CREDENTIALS_JSON;
    if (!raw) throw new Error('GOOGLE_CREDENTIALS_JSON env var is required');
    const creds = JSON.parse(raw);
    const auth = new google.auth.GoogleAuth({
        credentials: creds,
        scopes: ['https://www.googleapis.com/auth/drive'],
    });
    driveClient = google.drive({ version: 'v3', auth });
    return driveClient;
}

async function findChildFolder(drive, parentId, name) {
    const q = `'${parentId}' in parents and name='${name.replace(/'/g, "\\'")}' and mimeType='${FOLDER_MIME}' and trashed=false`;
    const res = await drive.files.list({
        q,
        fields: 'files(id, name)',
        spaces: 'drive',
        supportsAllDrives: true,
        includeItemsFromAllDrives: true,
    });
    return res.data.files?.[0]?.id || null;
}

async function ensureSubfolder(drive, parentId, name) {
    const existing = await findChildFolder(drive, parentId, name);
    if (existing) return existing;
    const created = await drive.files.create({
        requestBody: { name, mimeType: FOLDER_MIME, parents: [parentId] },
        fields: 'id',
        supportsAllDrives: true,
    });
    return created.data.id;
}

/**
 * Ensure folder path under rootFolderId exists, return innermost folder ID.
 * `pathParts` is e.g. ['Market Reports', '2026', '04'].
 */
async function ensureFolderPath(drive, rootFolderId, pathParts) {
    const cacheKey = pathParts.join('/');
    if (folderCache.has(cacheKey)) return folderCache.get(cacheKey);
    let parent = rootFolderId;
    for (const part of pathParts) {
        parent = await ensureSubfolder(drive, parent, part);
    }
    folderCache.set(cacheKey, parent);
    return parent;
}

/**
 * Upload `pdfBuffer` to <rootFolderId>/<pathParts...>/<filename>.
 * Returns { fileId, webViewLink }.
 */
export async function uploadPdf(pdfBuffer, { rootFolderId, pathParts, filename }) {
    const drive = getDrive();
    const folderId = await ensureFolderPath(drive, rootFolderId, pathParts);
    const res = await drive.files.create({
        requestBody: { name: filename, parents: [folderId] },
        media: { mimeType: 'application/pdf', body: Readable.from(pdfBuffer) },
        fields: 'id, webViewLink',
        supportsAllDrives: true,
    });
    log.info(`☁️  Uploaded ${filename} to Drive (${res.data.id})`);
    return { fileId: res.data.id, webViewLink: res.data.webViewLink };
}
```

- [ ] **Step 2: Commit**

```bash
git add actors/platts-scrap-reports/src/storage/gdriveUpload.js
git commit -m "feat(reports): add Google Drive upload with folder hierarchy"
```

---

### Task 12: notify/telegramSend.js — sendDocument with caption

**Files:**
- Create: `actors/platts-scrap-reports/src/notify/telegramSend.js`

- [ ] **Step 1: Implement telegramSend**

Create `actors/platts-scrap-reports/src/notify/telegramSend.js`:

```javascript
import fetch from 'node-fetch';
import { log } from 'crawlee';

const TG_API = 'https://api.telegram.org/bot';

function escapeMd(s) {
    if (!s) return '';
    return String(s).replace(/([_*`\[\]])/g, '\\$1');
}

export function buildCaption(row) {
    const name = escapeMd(row.reportName);
    const cover = escapeMd(row.coverDate || '—');
    const pub = escapeMd(row.publishedDate || '—');
    const freq = escapeMd(row.frequency || '—');
    return `📊 *${name}*\nCobertura: ${cover}\nPublicado: ${pub}\nFrequência: ${freq}`;
}

/**
 * Send a PDF buffer as a Telegram document with a Markdown caption.
 * Throws on non-2xx response.
 */
export async function sendPdfDocument(botToken, chatId, pdfBuffer, filename, caption) {
    if (!botToken) throw new Error('TELEGRAM_BOT_TOKEN required');
    if (!chatId) throw new Error('chatId required');

    const form = new FormData();
    form.append('chat_id', String(chatId));
    form.append('caption', caption);
    form.append('parse_mode', 'Markdown');
    form.append('document', new Blob([pdfBuffer], { type: 'application/pdf' }), filename);

    const url = `${TG_API}${botToken}/sendDocument`;
    const res = await fetch(url, { method: 'POST', body: form });
    if (!res.ok) {
        const text = await res.text();
        throw new Error(`Telegram sendDocument failed: ${res.status} ${text}`);
    }
    const json = await res.json();
    log.info(`💬 Telegram document sent (message_id=${json.result?.message_id})`);
    return json;
}
```

- [ ] **Step 2: Commit**

```bash
git add actors/platts-scrap-reports/src/notify/telegramSend.js
git commit -m "feat(reports): add Telegram sendDocument with Markdown caption"
```

---

### Task 13: main.js orchestration

**Files:**
- Create: `actors/platts-scrap-reports/src/main.js`

- [ ] **Step 1: Write main.js**

Create `actors/platts-scrap-reports/src/main.js`:

```javascript
import { Actor } from 'apify';
import { chromium } from 'playwright';
import { log } from 'crawlee';

import { loginPlatts } from './auth/login.js';
import { navigateGrid } from './grid/navigateGrid.js';
import { extractRows } from './grid/extractRows.js';
import { applyExcludeFilter } from './filters/applyFilters.js';
import { capturePdf } from './download/capturePdf.js';
import { isSeen, markSeen, closeRedis } from './storage/redisDedup.js';
import { uploadPdf } from './storage/gdriveUpload.js';
import { sendPdfDocument, buildCaption } from './notify/telegramSend.js';
import { slugify } from './util/slug.js';
import { parsePublishedDate, datePartsFromIso } from './util/dates.js';

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
    gdriveFolderId,
    telegramChatId,
} = input;

const TG_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const TG_CHAT = telegramChatId || process.env.TELEGRAM_CHAT_ID;

if (!username || !password) {
    await Actor.fail('username and password are required');
}
if (!gdriveFolderId && !dryRun) {
    await Actor.fail('gdriveFolderId is required (set in input or .env)');
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

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ acceptDownloads: true });
const page = await ctx.newPage();
const pageLog = { info: (m) => log.info(m), warn: (m) => log.warning(m), error: (m) => log.error(m) };

try {
    const loginResult = await loginPlatts(page, username, password, pageLog);
    if (!loginResult.ok) {
        summary.type = 'error';
        summary.errors.push({ stage: 'login', message: loginResult.reason || 'unknown' });
        await Actor.pushData(summary);
        return;
    }

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
        log.info(`📊 ${reportType}: ${rows.length} total, ${filtered.length} after filter (cap ${maxReportsPerType})`);

        for (const row of filtered) {
            const slug = slugify(row.reportName);
            const dateKey = parsePublishedDate(row.publishedDate);
            if (!slug || !dateKey) {
                summary.errors.push({ stage: 'parse-row', reportName: row.reportName, message: 'missing slug or dateKey' });
                summary.type = 'partial';
                continue;
            }

            if (!forceRedownload && (await isSeen(slug, dateKey))) {
                summary.skipped.push({ slug, dateKey, reason: 'already-seen' });
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
            const drivePath = [reportType, parts.year, parts.month];

            if (dryRun) {
                summary.would_download.push({ slug, dateKey, drivePath: `${drivePath.join('/')}/${filename}`, sizeBytes: pdfBuffer.length });
                continue;
            }

            let driveFileId;
            try {
                const upload = await uploadPdf(pdfBuffer, { rootFolderId: gdriveFolderId, pathParts: drivePath, filename });
                driveFileId = upload.fileId;
            } catch (e) {
                summary.errors.push({ stage: 'drive-upload', reportName: row.reportName, message: e.message });
                summary.type = 'partial';
                continue;
            }

            try {
                await sendPdfDocument(TG_TOKEN, TG_CHAT, pdfBuffer, filename, buildCaption(row));
            } catch (e) {
                log.warning(`Telegram send failed for ${filename}: ${e.message}`);
                summary.errors.push({ stage: 'telegram', reportName: row.reportName, message: e.message });
                // Still mark seen — PDF is in Drive, re-sending later is worse than silence
            }

            await markSeen(slug, dateKey);
            summary.downloaded.push({ slug, dateKey, drivePath: `${drivePath.join('/')}/${filename}`, driveFileId });
        }
    }

    await Actor.pushData(summary);
} finally {
    await closeRedis();
    await ctx.close();
    await browser.close();
    await Actor.exit();
}
```

- [ ] **Step 2: Lint + commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF /actors/platts-scrap-reports"
npm run lint:fix || true
cd "/Users/bigode/Dev/Antigravity WF "
git add actors/platts-scrap-reports/src/main.js
git commit -m "feat(reports): wire main.js orchestration (login → grid → download → upload → notify)"
```

---

## Phase 5: Manual integration validation

### Task 14: dryRun smoke test against real portal (local)

**Files:**
- Create (temp, gitignored): `actors/platts-scrap-reports/.local-input.json`

- [ ] **Step 1: Create local input file**

Create `actors/platts-scrap-reports/.local-input.json` (gitignored — confirm with `git check-ignore`):

```json
{
    "username": "REPLACE_WITH_PLATTS_USERNAME",
    "password": "REPLACE_WITH_PLATTS_PASSWORD",
    "reportTypes": ["Market Reports"],
    "maxReportsPerType": 5,
    "dryRun": true,
    "gdriveFolderId": "1KxixMP9rKF0vGzINGvmmyFvouaOvL02y"
}
```

Then verify it's ignored:

```bash
cd "/Users/bigode/Dev/Antigravity WF "
git check-ignore actors/platts-scrap-reports/.local-input.json && echo "OK: ignored" || echo "WARN: NOT ignored — add .local-input.json to .gitignore"
```

If not ignored, add this line to root `.gitignore`:

```
actors/*/.local-input.json
```

- [ ] **Step 2: Run actor locally with dryRun**

```bash
cd "/Users/bigode/Dev/Antigravity WF /actors/platts-scrap-reports"
APIFY_LOCAL_STORAGE_DIR=./storage \
  npx apify run --input-file=.local-input.json
```

Expected behavior:
- Login succeeds
- Grid loads for Market Reports
- 5 rows extracted, English-only (default exclude filter applied)
- For each row: prints `[dryRun] would download <slug> ...`
- Final dataset item has `would_download[]` populated, `downloaded: []`, no errors

If errors occur, **return to Task 1 spike** to verify selectors/mechanism, fix the relevant module, re-run.

- [ ] **Step 3: Inspect output**

```bash
cat actors/platts-scrap-reports/storage/datasets/default/000000001.json
```

Verify `would_download[]` matches the rows visible in the screenshot (English originals only).

- [ ] **Step 4: Run full mode (not dry) for ONE report to verify full pipeline**

Edit `.local-input.json`: set `"dryRun": false`, `"maxReportsPerType": 1`. Re-run:

```bash
APIFY_LOCAL_STORAGE_DIR=./storage \
  TELEGRAM_BOT_TOKEN="$(grep ^TELEGRAM_BOT_TOKEN= ../../.env | cut -d= -f2-)" \
  TELEGRAM_CHAT_ID="$(grep ^TELEGRAM_CHAT_ID= ../../.env | cut -d= -f2-)" \
  REDIS_URL="$(grep ^REDIS_URL= ../../.env | cut -d= -f2-)" \
  GOOGLE_CREDENTIALS_JSON="$(grep ^GOOGLE_CREDENTIALS_JSON= ../../.env | cut -d= -f2-)" \
  npx apify run --input-file=.local-input.json
```

Verify:
- 1 PDF appears in Google Drive at `Platts Reports/Market Reports/<YYYY>/<MM>/<filename>.pdf`
- Telegram chat receives 1 document with the caption format
- Re-running same input → produces `skipped: [{...reason: "already-seen"}]`, no duplicate Drive file, no duplicate Telegram message

- [ ] **Step 5: Commit any selector/mechanism fixes from Task 14 iterations**

```bash
cd "/Users/bigode/Dev/Antigravity WF "
git add actors/platts-scrap-reports/src/
git commit -m "fix(reports): align selectors/download mechanism with live portal"
```

Skip this commit if no changes were needed.

---

## Phase 6: Deploy actor + Python wrapper + workflow

### Task 15: Push actor to Apify cloud

- [ ] **Step 1: Authenticate Apify CLI (one-time, if not done)**

```bash
npx apify login
```

- [ ] **Step 2: Push the actor**

```bash
cd "/Users/bigode/Dev/Antigravity WF /actors/platts-scrap-reports"
npx apify push
```

Expected: actor builds successfully on Apify, returns the actor ID (`bigodeio05/platts-scrap-reports`).

- [ ] **Step 3: Run a smoke test on Apify cloud via dashboard or CLI**

Trigger one run with `dryRun: true` from the Apify console using the same input as Task 14 Step 1. Verify `would_download[]` looks identical to the local run.

---

### Task 16: Python wrapper script

**Files:**
- Create: `execution/scripts/platts_reports.py`

- [ ] **Step 1: Write the wrapper**

Create `execution/scripts/platts_reports.py`:

```python
#!/usr/bin/env python3
"""Trigger the platts-scrap-reports Apify actor and record run state.

Scheduled daily via .github/workflows/platts_reports.yml.
"""
import argparse
import json
import os
import sys
import traceback

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from execution.core import state_store
from execution.integrations.apify_client import ApifyClient

ACTOR_ID = os.getenv("APIFY_PLATTS_REPORTS_ACTOR_ID", "bigodeio05/platts-scrap-reports")
WORKFLOW_NAME = "platts_reports"


def main() -> int:
    parser = argparse.ArgumentParser(description="Trigger Platts Reports actor")
    parser.add_argument("--dry-run", action="store_true", help="Pass dryRun=true to the actor")
    parser.add_argument("--force-redownload", action="store_true")
    args = parser.parse_args()

    username = os.environ.get("PLATTS_USERNAME")
    password = os.environ.get("PLATTS_PASSWORD")
    if not username or not password:
        print("ERROR: PLATTS_USERNAME and PLATTS_PASSWORD required", file=sys.stderr)
        return 2

    run_input = {
        "username": username,
        "password": password,
        "reportTypes": ["Market Reports", "Research Reports"],
        "maxReportsPerType": 50,
        "dryRun": args.dry_run,
        "forceRedownload": args.force_redownload,
        "gdriveFolderId": os.environ.get("GDRIVE_PLATTS_REPORTS_FOLDER_ID", "1KxixMP9rKF0vGzINGvmmyFvouaOvL02y"),
    }

    print(f"🚀 Triggering actor {ACTOR_ID} (dryRun={args.dry_run})")
    client = ApifyClient()
    try:
        dataset_id = client.run_actor(ACTOR_ID, run_input, memory_mbytes=4096, timeout_secs=900)
        items = client.get_dataset_items(dataset_id)
    except Exception as e:
        print(f"❌ Actor run failed: {e}", file=sys.stderr)
        traceback.print_exc()
        state_store.record_failure(WORKFLOW_NAME, str(e))
        return 1

    if not items:
        print("⚠️  Actor returned no dataset items")
        state_store.record_empty(WORKFLOW_NAME)
        return 0

    summary = items[0]
    print(json.dumps(summary, indent=2, default=str))

    summary_for_state = {
        "type": summary.get("type"),
        "downloaded_count": len(summary.get("downloaded", [])),
        "skipped_count": len(summary.get("skipped", [])),
        "errors_count": len(summary.get("errors", [])),
    }
    if summary.get("type") == "error":
        state_store.record_failure(WORKFLOW_NAME, json.dumps(summary_for_state))
        return 1
    state_store.record_success(WORKFLOW_NAME, summary_for_state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Add env var to .env**

```bash
cd "/Users/bigode/Dev/Antigravity WF "
grep -q '^GDRIVE_PLATTS_REPORTS_FOLDER_ID=' .env || echo 'GDRIVE_PLATTS_REPORTS_FOLDER_ID=1KxixMP9rKF0vGzINGvmmyFvouaOvL02y' >> .env
```

- [ ] **Step 3: Smoke test the wrapper locally with --dry-run**

```bash
cd "/Users/bigode/Dev/Antigravity WF "
.venv/bin/python -m execution.scripts.platts_reports --dry-run
```

Expected: triggers Apify cloud run, prints the same `would_download[]` output as Task 14.

- [ ] **Step 4: Commit**

```bash
git add execution/scripts/platts_reports.py
git commit -m "feat(scripts): add platts_reports wrapper triggering Apify actor"
```

---

### Task 17: GitHub Actions workflow

**Files:**
- Create: `.github/workflows/platts_reports.yml`

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/platts_reports.yml`:

```yaml
name: "Platts Reports (PDF Downloader)"

on:
  schedule:
    # 13:00 UTC = 10:00 BRT, daily including weekends
    - cron: '0 13 * * *'
  workflow_dispatch:
    inputs:
      dry_run:
        description: 'Dry Run (sem upload/notificação)'
        required: false
        type: boolean
        default: false
      force_redownload:
        description: 'Re-baixar tudo ignorando dedup'
        required: false
        type: boolean
        default: false

jobs:
  download-and-distribute:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.10'

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run Platts Reports
        env:
          APIFY_API_TOKEN: ${{ secrets.APIFY_API_TOKEN }}
          APIFY_PLATTS_REPORTS_ACTOR_ID: bigodeio05/platts-scrap-reports
          PLATTS_USERNAME: ${{ secrets.PLATTS_USERNAME }}
          PLATTS_PASSWORD: ${{ secrets.PLATTS_PASSWORD }}
          GDRIVE_PLATTS_REPORTS_FOLDER_ID: ${{ secrets.GDRIVE_PLATTS_REPORTS_FOLDER_ID }}
          GOOGLE_CREDENTIALS_JSON: ${{ secrets.GOOGLE_CREDENTIALS_JSON }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          REDIS_URL: ${{ secrets.REDIS_URL }}
        run: |
          DRY=""
          FORCE=""
          if [ "${{ github.event.inputs.dry_run }}" = "true" ]; then DRY="--dry-run"; fi
          if [ "${{ github.event.inputs.force_redownload }}" = "true" ]; then FORCE="--force-redownload"; fi
          python -m execution.scripts.platts_reports $DRY $FORCE
```

- [ ] **Step 2: Add the GH Actions secret**

Manual one-time step (not automatable from this plan):

1. Go to GitHub repo → Settings → Secrets and variables → Actions
2. Add new repository secret: `GDRIVE_PLATTS_REPORTS_FOLDER_ID = 1KxixMP9rKF0vGzINGvmmyFvouaOvL02y`
3. Verify other secrets already exist: `APIFY_API_TOKEN`, `PLATTS_USERNAME`, `PLATTS_PASSWORD`, `GOOGLE_CREDENTIALS_JSON`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `REDIS_URL`

- [ ] **Step 3: Commit + push**

```bash
cd "/Users/bigode/Dev/Antigravity WF "
git add .github/workflows/platts_reports.yml
git commit -m "ci: schedule platts_reports daily at 13:00 UTC (10:00 BRT)"
git push origin main
```

- [ ] **Step 4: Trigger workflow_dispatch with dryRun=true**

GitHub UI → Actions → "Platts Reports (PDF Downloader)" → Run workflow → check Dry Run → run.

Expected: green build, log shows `would_download[]` populated.

---

## Phase 7: Final validation

### Task 18: Production smoke run (real download)

- [ ] **Step 1: Trigger workflow_dispatch with defaults (dryRun=false)**

GitHub UI → Actions → "Platts Reports (PDF Downloader)" → Run workflow → leave both inputs unchecked → run.

- [ ] **Step 2: Verify**

- [ ] PDFs appear in Google Drive at `Platts Reports/Market Reports/2026/04/` and `Platts Reports/Research Reports/2026/04/`
- [ ] Telegram chat receives `sendDocument` for each new PDF with the formatted caption
- [ ] No duplicates (filenames are unique per report+date)
- [ ] Re-running immediately after → `skipped[]` with all already-seen, zero new downloads
- [ ] `wf:last_run:platts_reports` in Redis shows status=success and downloaded_count > 0

- [ ] **Step 3: Update memory with new project facts**

Save a project memory summarizing the new actor (Redis key, cron, Drive folder location) so future sessions are aware. Save to `/Users/bigode/.claude/projects/-Users-bigode-Dev-Antigravity-WF-/memory/project_reports_actor.md` with frontmatter and a one-line entry in `MEMORY.md`.

---

## Self-review

**Spec coverage check:**
- Login flow → Task 2 + Task 13 ✓
- Grid navigation → Task 7 ✓
- Row extraction → Task 8 ✓
- Translation filter → Task 5 ✓
- PDF capture → Task 9 ✓
- Drive upload + folder hierarchy → Task 11 ✓
- Redis dedup (`platts:report:seen:<slug>:<date>`, 90d) → Task 10 ✓
- Telegram `sendDocument` → Task 12 ✓
- Input schema → Task 6 ✓
- Error handling matrix → wired in Task 13 (per stage: login aborts, grid retries 1x, download/upload skip, telegram marks seen anyway) — mostly covered; **gap:** spec says grid timeout retries 1x with 5s backoff, but Task 13 only catches+continues. Add retry inline in Task 7 if behavior matters; for now `partial` summary type captures it.
- Schedule + deploy → Task 17 ✓
- Open items (selectors, download mechanism, etc) → Task 1 spike ✓
- Success criteria 1–6 → all walked in Tasks 14, 18 ✓

**Placeholder scan:**
- `<GRID_READY_SELECTOR>`, `<ROW_SELECTOR>`, `<REPORT_NAME_CELL>`, etc. in Tasks 7–9 are explicit placeholders that depend on Task 1 spike output. Each task notes this dependency clearly with "Replace `<...>` with values from `actors/.investigation/reports-spike.md`." Acceptable — the spike IS the way to fill them; cannot be guessed.
- Task 9 has 3 implementation branches (A/B/C) and instructs "Pick ONE based on spike, delete the others" — explicit, not a placeholder problem.
- No "TODO/TBD/implement later" elsewhere.

**Type consistency:**
- `slug`, `dateKey` (string YYYY-MM-DD), `rowIndex` (int), `pdfBuffer` (Buffer), `gdriveFolderId` (string) consistent across modules
- `seenKey()` format `platts:report:seen:<slug>:<dateKey>` consistent with spec
- Drive path parts `[reportType, year, month]` consistent across `main.js` and `gdriveUpload.js`
- `summary` field names match `dataset_schema.json`

Plan is internally consistent and ready for execution.
