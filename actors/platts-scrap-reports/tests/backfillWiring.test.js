import { describe, expect, it } from 'vitest';

import { resolveBackfillPublications } from '../src/backfill/runBackfill.js';
import { DEFAULT_EXCLUDES } from '../src/filters/applyFilters.js';

const gridRows = [
    { reportName: 'Steel Price Report' },
    { reportName: 'Steel Business Briefing' },
    { reportName: 'Cement Weekly' },
    { reportName: 'Panorama Semanal' },
    { reportName: 'Global Market Outlook (Português)' },
    { reportName: 'Global Market Outlook' },
];

describe('resolveBackfillPublications', () => {
    it('usa os nomes do grid menos os excludes padrão', () => {
        expect(resolveBackfillPublications(gridRows, DEFAULT_EXCLUDES)).toEqual([
            'Steel Price Report',
            'Steel Business Briefing',
            'Cement Weekly',
            'Global Market Outlook',
        ]);
    });

    it('remove nomes repetidos', () => {
        const dup = [...gridRows, { reportName: 'Cement Weekly' }];
        const out = resolveBackfillPublications(dup, DEFAULT_EXCLUDES);
        expect(out.filter((n) => n === 'Cement Weekly')).toHaveLength(1);
    });

    it('devolve vazio quando o grid não trouxe nada', () => {
        expect(resolveBackfillPublications([], DEFAULT_EXCLUDES)).toEqual([]);
    });
});
