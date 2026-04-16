import { describe, expect,it } from 'vitest';

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
