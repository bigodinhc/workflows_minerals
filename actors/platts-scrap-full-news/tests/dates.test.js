import { describe, it, expect, beforeAll, afterAll, vi } from 'vitest';
import { isDateWithinFilter, parsePlattsDate, formatDateBR } from '../src/util/dates.js';

// Pin "now" so lastXDays tests don't drift with calendar time. Picked a
// non-ambiguous day (>12) to sidestep the known DD/MM vs MM/DD parser quirk.
const FAKE_NOW = new Date(Date.UTC(2026, 3, 16)); // 16 April 2026 UTC

beforeAll(() => {
    vi.useFakeTimers();
    vi.setSystemTime(FAKE_NOW);
});

afterAll(() => {
    vi.useRealTimers();
});

describe('isDateWithinFilter', () => {
    describe('specificDate', () => {
        it('returns true when article date matches targetDate', () => {
            // Bug guard: when caller sends dateFilter=specificDate + targetDate,
            // the actor must filter to that exact day. Earlier the caller forced
            // dateFilter="all" and targetDate was silently ignored.
            const articleDate = '16/04/2026 12:34:56 UTC';
            const targetDate = '16/04/2026';
            expect(isDateWithinFilter(articleDate, 'specificDate', 1, targetDate)).toBe(true);
        });

        it('returns false when article date is one day before targetDate', () => {
            const articleDate = '15/04/2026 23:59:59 UTC';
            const targetDate = '16/04/2026';
            expect(isDateWithinFilter(articleDate, 'specificDate', 1, targetDate)).toBe(false);
        });

        it('returns false when article date is one day after targetDate', () => {
            const articleDate = '17/04/2026 00:00:01 UTC';
            const targetDate = '16/04/2026';
            expect(isDateWithinFilter(articleDate, 'specificDate', 1, targetDate)).toBe(false);
        });
    });

    describe('today', () => {
        it('uses targetDate as "today" when explicitly provided', () => {
            const articleDate = '16/04/2026 09:00:00 UTC';
            const targetDate = '16/04/2026';
            expect(isDateWithinFilter(articleDate, 'today', 1, targetDate)).toBe(true);
        });

        it('rejects dates outside the target day', () => {
            const articleDate = '14/04/2026 10:00:00 UTC';
            const targetDate = '16/04/2026';
            expect(isDateWithinFilter(articleDate, 'today', 1, targetDate)).toBe(false);
        });
    });

    describe('lastXDays', () => {
        // lastXDays ignores targetDate by design (dates.js:92-93). Window is
        // anchored to "now" — so these tests pin system time via vi.setSystemTime.
        it('accepts article inside the window', () => {
            // FAKE_NOW = 16/04. window with daysBack=3 → cutoff 13/04.
            expect(isDateWithinFilter('15/04/2026 10:00:00 UTC', 'lastXDays', 3, null)).toBe(true);
        });

        it('rejects article older than the window', () => {
            // 28/03 < cutoff 13/04 → out of window. Day 28 > 12 keeps parser unambiguous.
            expect(isDateWithinFilter('28/03/2026 10:00:00 UTC', 'lastXDays', 3, null)).toBe(false);
        });

        it('accepts the boundary day (cutoff = now - daysBack)', () => {
            expect(isDateWithinFilter('13/04/2026 10:00:00 UTC', 'lastXDays', 3, null)).toBe(true);
        });

        it('ignores targetDate even when supplied', () => {
            // Sanity: targetDate is silently dropped on lastXDays. If this ever
            // becomes a feature, update both code and this test together.
            expect(isDateWithinFilter('15/04/2026 10:00:00 UTC', 'lastXDays', 3, '01/01/2020')).toBe(true);
        });
    });

    describe('all', () => {
        it('returns true regardless of date', () => {
            // Sanity: filter "all" must not be silently the same as specificDate.
            // Distinct branch matters because the caller bug confused these.
            expect(isDateWithinFilter('01/01/2020 00:00:00 UTC', 'all', 1, '16/04/2026')).toBe(true);
            expect(isDateWithinFilter('99/99/9999', 'all', 1, null)).toBe(true);
        });
    });

    describe('unparseable input', () => {
        it('returns true only when filter is "all"', () => {
            expect(isDateWithinFilter('garbage', 'all', 1, null)).toBe(true);
            expect(isDateWithinFilter('garbage', 'today', 1, '16/04/2026')).toBe(false);
            expect(isDateWithinFilter('garbage', 'specificDate', 1, '16/04/2026')).toBe(false);
        });

        it('accepts relative-time strings under a multi-day window', () => {
            // Relative strings parse via parseRelativeTime which uses local-time
            // setHours; under UTC-anchored "today" filtering this can drift one day.
            // lastXDays(2) is wide enough to absorb the timezone slack.
            expect(isDateWithinFilter('há 2 horas', 'lastXDays', 2, null)).toBe(true);
            expect(isDateWithinFilter('5 minutes ago', 'lastXDays', 2, null)).toBe(true);
        });
    });
});

describe('parsePlattsDate', () => {
    it('parses DD/MM/YYYY HH:MM:SS UTC unambiguously when day > 12', () => {
        const d = parsePlattsDate('16/04/2026 12:34:56 UTC');
        expect(d).not.toBeNull();
        expect(d.getUTCFullYear()).toBe(2026);
        expect(d.getUTCMonth()).toBe(3); // April = 3
        expect(d.getUTCDate()).toBe(16);
    });

    it('returns null for unparseable input', () => {
        expect(parsePlattsDate('')).toBeNull();
        expect(parsePlattsDate('not a date')).toBeNull();
        expect(parsePlattsDate(null)).toBeNull();
    });
});

describe('formatDateBR', () => {
    it('formats a Date as DD/MM/YYYY UTC', () => {
        const d = new Date(Date.UTC(2026, 3, 16));
        expect(formatDateBR(d)).toBe('16/04/2026');
    });

    it('returns DATA INVÁLIDA for invalid input', () => {
        expect(formatDateBR(null)).toBe('DATA INVÁLIDA');
        expect(formatDateBR(new Date('not a date'))).toBe('DATA INVÁLIDA');
    });
});
