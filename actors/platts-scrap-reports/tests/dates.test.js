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
