import { describe, expect, it } from 'vitest';

import { buildSearchPayload, isoWindow } from '../src/api/searchPayload.js';

describe('isoWindow', () => {
    it('cobre o dia inteiro nas duas pontas', () => {
        expect(isoWindow('2025-01-01', '2025-12-31')).toEqual({
            fromDate: '2025-01-01T00:00:00.000Z',
            toDate: '2025-12-31T23:59:59.999Z',
        });
    });

    it('rejeita formato que não seja YYYY-MM-DD', () => {
        expect(() => isoWindow('01/01/2025', '2025-12-31')).toThrow(/YYYY-MM-DD/);
    });
});

describe('buildSearchPayload', () => {
    const base = { publication: 'Steel Price Report', fromDate: '2025-01-01T00:00:00.000Z', toDate: '2025-12-31T23:59:59.999Z' };

    it('reproduz o corpo que a própria UI do Platts envia', () => {
        expect(buildSearchPayload({ ...base, page: 1 })).toEqual({
            sector: [], geography: [], brand: [], commodity: [],
            contentType: ['Market Reports'],
            linkType: '', mimeType: '',
            subjectType: 'Subject', subject: [],
            fromDate: '2025-01-01T00:00:00.000Z',
            toDate: '2025-12-31T23:59:59.999Z',
            page: 1, pageSize: 50,
            query: '() AND ()',
            isArchived: true,
            publication: ['Steel Price Report'],
            frequency: [], groupBy: '',
            isAllPublicationsSelected: false,
            language: [],
            isChapteredContent: true,
            sort: '',
            spotEnabled: true,
        });
    });

    it('isArchived é sempre true — é o que alcança o histórico', () => {
        expect(buildSearchPayload({ ...base, page: 3 }).isArchived).toBe(true);
    });

    it('propaga a página pedida', () => {
        expect(buildSearchPayload({ ...base, page: 4 }).page).toBe(4);
    });
});
