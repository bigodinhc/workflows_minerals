import { JSDOM } from 'jsdom';
import { describe, expect, it } from 'vitest';

import { REPORT_COLUMNS } from '../src/grid/columns.js';
import { extractRows } from '../src/grid/extractRows.js';
import { scrapeRowsFromDom } from '../src/grid/scrapeRows.js';

/**
 * Mirrors the real Platts AG-Grid as captured on 2026-08-28: three sibling
 * containers (pinned-left / center / pinned-right) each render their own
 * .ag-row for the SAME row-index. Extraction must not triple-count.
 */
function buildGrid(rows, { nameColId = 'Title' } = {}) {
    const left = rows
        .map((_, i) => `<div class="ag-row" row-index="${i}"><div class="ag-cell" col-id="Checkbox"></div></div>`)
        .join('');
    const center = rows
        .map(
            (r, i) => `<div class="ag-row" row-index="${i}">
                <div class="ag-cell" col-id="${nameColId}"><span>${r.name}</span></div>
                <div class="ag-cell" col-id="ReportTitle"></div>
                <div class="ag-cell" col-id="Frequency">${r.frequency}</div>
                <div class="ag-cell" col-id="__coverDateOriginal">${r.coverDate}</div>
                <div class="ag-cell" col-id="UpdatedDate">${r.updated}</div>
            </div>`,
        )
        .join('');
    const right = rows
        .map(
            (_, i) => `<div class="ag-row" row-index="${i}">
                <div class="ag-cell" col-id="Action">
                    <button aria-label="Add to Bookmarks"><i class="far fa-bookmark"></i></button>
                    <button aria-label="Archive"><i class="far fa-archive"></i></button>
                    <div tabindex="0" aria-label="Download PDF" role="button"><svg></svg></div>
                </div>
                <div class="ag-cell" col-id="ThreeDotMenu"></div>
            </div>`,
        )
        .join('');

    const html = `<body>
        <div class="ag-pinned-left-cols-container">${left}</div>
        <div class="ag-center-cols-container">${center}</div>
        <div class="ag-pinned-right-cols-container">${right}</div>
    </body>`;
    return new JSDOM(html).window.document;
}

const SAMPLE = [
    { name: 'Steel Price Report', frequency: 'Daily', coverDate: '27 Aug 2026', updated: '27/08/2026 21:54:13 UTC' },
    { name: 'Steel Business Briefing', frequency: 'Daily', coverDate: '27 Aug 2026', updated: '27/08/2026 21:48:54 UTC' },
];

/** Stand-in for a Playwright page: runs the evaluate callback against jsdom. */
const fakePage = (doc) => ({
    evaluate: async (fn, arg) => fn({ ...arg, doc }),
});

describe('scrapeRowsFromDom', () => {
    it('returns one row per distinct row-index despite pinned-container mirroring', () => {
        const doc = buildGrid(SAMPLE);
        expect(doc.querySelectorAll('.ag-row')).toHaveLength(6); // 2 rows x 3 containers
        const { rows } = scrapeRowsFromDom({ columns: REPORT_COLUMNS, doc });
        expect(rows).toHaveLength(2);
    });

    it('maps the current Platts col-ids onto domain field names', () => {
        const doc = buildGrid(SAMPLE);
        const { rows } = scrapeRowsFromDom({ columns: REPORT_COLUMNS, doc });
        expect(rows[0]).toMatchObject({
            rowIndex: 0,
            reportName: 'Steel Price Report',
            frequency: 'Daily',
            coverDate: '27 Aug 2026',
            publishedDate: '27/08/2026 21:54:13 UTC',
        });
    });

    it('reports the col-ids actually present, for diagnosing future grid changes', () => {
        const doc = buildGrid(SAMPLE);
        const { colIdsSeen } = scrapeRowsFromDom({ columns: REPORT_COLUMNS, doc });
        expect(colIdsSeen).toEqual(
            expect.arrayContaining(['Checkbox', 'Title', 'Frequency', '__coverDateOriginal', 'UpdatedDate', 'Action']),
        );
    });
});

describe('extractRows', () => {
    it('drops rows that have no report name', async () => {
        const doc = buildGrid([...SAMPLE, { name: '', frequency: '', coverDate: '', updated: '' }]);
        const rows = await extractRows(fakePage(doc));
        expect(rows.map((r) => r.reportName)).toEqual(['Steel Price Report', 'Steel Business Briefing']);
    });

    it('throws when rows render but the name column no longer matches', async () => {
        // Exactly the 2026-08-01 breakage: grid renders, col-id renamed.
        const doc = buildGrid(SAMPLE, { nameColId: 'SomeRenamedColumn' });
        await expect(extractRows(fakePage(doc))).rejects.toThrow(/grid layout changed/i);
    });

    it('names the observed col-ids in the failure message', async () => {
        const doc = buildGrid(SAMPLE, { nameColId: 'SomeRenamedColumn' });
        await expect(extractRows(fakePage(doc))).rejects.toThrow(/SomeRenamedColumn/);
    });

    it('does not throw on a genuinely empty grid', async () => {
        const doc = buildGrid([]);
        await expect(extractRows(fakePage(doc))).resolves.toEqual([]);
    });
});
