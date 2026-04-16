import { describe, expect,it } from 'vitest';

import { datePartsFromIso,parsePublishedDate } from '../src/util/dates.js';

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

    it('disambiguates MM/DD/YYYY when first group ≤ 12 and second > 12', () => {
        // Apify en-US locale: "04/15/2026" = April 15
        expect(parsePublishedDate('04/15/2026 21:29:02 UTC')).toBe('2026-04-15');
    });

    it('disambiguates DD/MM/YYYY when first group > 12', () => {
        // PT locale: "15/04/2026" = April 15
        expect(parsePublishedDate('15/04/2026 21:29:02 UTC')).toBe('2026-04-15');
    });

    it('defaults to DD/MM when both ≤ 12 (European convention)', () => {
        // "05/04/2026" → ambiguous → default DD=05, MM=04 → April 5
        expect(parsePublishedDate('05/04/2026')).toBe('2026-04-05');
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
