/**
 * The 2024 run recorded one supabase-upload error in its summary and the run
 * log contained zero occurrences of the word "failed". An error nobody can
 * find in the log is an error nobody diagnoses, so every recorded failure has
 * to reach the log too.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const warning = vi.fn();
vi.mock('crawlee', () => ({
    log: { info: vi.fn(), warning, error: vi.fn(), debug: vi.fn() },
}));

const { runBackfill } = await import('../src/backfill/runBackfill.js');

const PDF = Buffer.from('%PDF-1.7 x');

function pageOf(publication, dates) {
    return {
        Items: dates.map((d) => ({
            Name: `${d}.pdf`,
            ReportName: publication,
            CoverDate: `${d}T00:00:00.000Z`,
            UpdatedDate: `${d}T21:54:13Z`,
            Frequency: 'Daily',
            PublicationGrouping: [{ Id: `id-${d}`, MimeType: 'application/pdf', Name: `${d}.pdf` }],
        })),
        TotalPages: 1,
        TotalRecordCount: dates.length,
        Page: 1,
        PageSize: 50,
    };
}

function deps(overrides = {}) {
    return {
        api: {
            searchArchive: vi.fn(async () => pageOf('Steel Price Report', ['2025-01-02', '2025-01-03'])),
            fetchPdf: vi.fn(async () => PDF),
        },
        publications: ['Steel Price Report'],
        fromDate: '2025-01-01T00:00:00.000Z',
        toDate: '2025-12-31T23:59:59.999Z',
        reportType: 'Market Reports',
        concurrency: 2,
        isAlreadyStored: vi.fn(async () => false),
        uploadPdf: vi.fn(async () => ({ id: 'row-1' })),
        now: () => 0,
        ...overrides,
    };
}

describe('backfill error logging', () => {
    beforeEach(() => warning.mockClear());

    it('logs an upload failure, not just records it in the summary', async () => {
        // One item succeeds: an all-failing run trips the zero-yield guard
        // instead, which is a different code path from the one under test.
        const d = deps({
            uploadPdf: vi.fn(async (_buf, { storagePath }) => {
                if (storagePath.includes('2025-01-02')) {
                    throw new Error('Storage upload failed: name=StorageApiError | status=546');
                }
                return { id: 'row-1' };
            }),
        });

        const summary = await runBackfill(d);

        expect(summary.errors).toHaveLength(1);
        expect(summary.downloaded).toHaveLength(1);
        const logged = warning.mock.calls.map((c) => String(c[0])).join('\n');
        expect(logged).toContain('supabase-upload');
        expect(logged).toContain('Steel Price Report');
        expect(logged).toContain('status=546');
    });

    it('logs a download failure with its stage', async () => {
        const d = deps({
            api: {
                searchArchive: vi.fn(async () => pageOf('Steel Price Report', ['2025-01-02', '2025-01-03'])),
                fetchPdf: vi.fn(async (id) => {
                    if (id === 'id-2025-01-02') throw new Error('stream 504');
                    return PDF;
                }),
            },
        });

        await runBackfill(d);

        const logged = warning.mock.calls.map((c) => String(c[0])).join('\n');
        expect(logged).toContain('download');
        expect(logged).toContain('stream 504');
    });

    it('says nothing when every item succeeds', async () => {
        await runBackfill(deps());

        expect(warning).not.toHaveBeenCalled();
    });
});
