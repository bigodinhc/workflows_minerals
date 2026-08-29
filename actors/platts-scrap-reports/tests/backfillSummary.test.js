import { describe, expect, it } from 'vitest';

import { buildBackfillSummaryText } from '../src/notify/backfillSummary.js';

const summary = {
    downloaded: new Array(912).fill({}),
    skipped: new Array(15).fill({}),
    errors: [
        { stage: 'download', message: 'is ZIP/Office container' },
        { stage: 'download', message: 'is ZIP/Office container' },
        { stage: 'supabase-upload', message: 'Storage upload failed' },
    ],
    publications: [
        { publication: 'Steel Price Report', totalRecords: 249, processed: 249 },
        { publication: 'Cement Weekly', totalRecords: 52, processed: 52 },
    ],
    durationMs: 1_500_000,
};

describe('buildBackfillSummaryText', () => {
    it('mostra os totais que importam', () => {
        const text = buildBackfillSummaryText(summary);
        expect(text).toContain('912');
        expect(text).toContain('15');
    });

    it('agrupa os erros por etapa em vez de listar um a um', () => {
        const text = buildBackfillSummaryText(summary);
        expect(text).toContain('download: 2');
        expect(text).toContain('supabase-upload: 1');
    });

    it('mostra a duração em minutos', () => {
        expect(buildBackfillSummaryText(summary)).toContain('25 min');
    });

    it('não usa marcação Markdown — o texto vai com parse_mode desligado', () => {
        const text = buildBackfillSummaryText(summary);
        expect(text).not.toMatch(/[*_`]/);
    });

    it('lida com um backfill sem nenhum erro', () => {
        const text = buildBackfillSummaryText({ ...summary, errors: [] });
        expect(text).toContain('sem erros');
    });
});
