import { describe, expect, it } from 'vitest';

import { dateKeyFromCoverDate, paginationOf, parseItems } from '../src/api/parseItems.js';
import fixture from './fixtures/blendedsearch-response.json';

describe('dateKeyFromCoverDate', () => {
    it('extrai YYYY-MM-DD do ISO', () => {
        expect(dateKeyFromCoverDate('2026-08-27T00:00:00.000Z')).toBe('2026-08-27');
    });

    it('não desloca o dia em fuso negativo', () => {
        const cover = '2026-08-27T00:00:00.000Z';
        // A abordagem ingênua, para contraste: em UTC-3 isto é dia 26.
        const naive = new Date(cover).toLocaleDateString('en-CA', { timeZone: 'America/Sao_Paulo' });
        expect(naive).toBe('2026-08-26');
        // A nossa não passa por Date.
        expect(dateKeyFromCoverDate(cover)).toBe('2026-08-27');
    });

    it('devolve null para entrada inválida', () => {
        expect(dateKeyFromCoverDate('')).toBeNull();
        expect(dateKeyFromCoverDate(null)).toBeNull();
        expect(dateKeyFromCoverDate('27/08/2026')).toBeNull();
    });
});

describe('parseItems', () => {
    it('escolhe o Id do application/pdf, não o do XML', () => {
        const rows = parseItems(fixture);
        expect(rows[0].id).toBe('8174a051-a9a5-43c7-9817-e8257efec05a');
        expect(rows[0].fileName).toBe('SPR_20260827.pdf');
    });

    it('descarta item sem PDF no PublicationGrouping', () => {
        const rows = parseItems(fixture);
        expect(rows).toHaveLength(2);
        expect(rows.map((r) => r.dateKey)).toEqual(['2026-08-27', '2026-08-26']);
    });

    it('normaliza os campos que o upload precisa', () => {
        expect(parseItems(fixture)[0]).toEqual({
            id: '8174a051-a9a5-43c7-9817-e8257efec05a',
            fileName: 'SPR_20260827.pdf',
            reportName: 'Steel Price Report',
            dateKey: '2026-08-27',
            frequency: 'Daily',
            coverDate: '2026-08-27T00:00:00.000Z',
        });
    });

    it('devolve lista vazia quando não há Items', () => {
        expect(parseItems({})).toEqual([]);
        expect(parseItems(null)).toEqual([]);
    });
});

describe('paginationOf', () => {
    it('lê os campos explícitos da resposta', () => {
        expect(paginationOf(fixture)).toEqual({ page: 1, totalPages: 5, totalRecords: 249 });
    });

    it('assume zero páginas quando a resposta não traz os campos', () => {
        expect(paginationOf({})).toEqual({ page: 1, totalPages: 0, totalRecords: 0 });
    });
});
