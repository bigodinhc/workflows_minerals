import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

import { detectDateFormat,formatDateBR, isDateWithinFilter, parsePlattsDate } from '../src/util/dates.js';

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

        it('matches a same-day ambiguous MM/DD grid date (IODEX regression)', () => {
            // The bug: Platts headless renders MM/DD, so the IODEX grid showed today's
            // rows as "06/11/2026" (11 Jun). The old DD/MM fallback read that as 6 Nov
            // and dropped all 12 rows ("0 dentro do filtro"). targetDate input is DD/MM.
            const articleDate = '06/11/2026 15:10:14 UTC'; // 11 Jun, MM/DD
            const targetDate = '11/06/2026';               // 11 Jun, DD/MM input
            expect(isDateWithinFilter(articleDate, 'today', 1, targetDate)).toBe(true);
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

    it('treats ambiguous "05/03/2026" as MM/DD (May 3) by default', () => {
        // The headless Platts Connect session renders MM/DD/YYYY (verified in prod:
        // FLASH banner "02/20/2026" and same-day article timestamps "06/11/2026" on
        // 11 Jun). So the ambiguous fallback reads month-first. Earlier code assumed
        // DD/MM here and silently dropped today's IODEX rows from the "today" filter.
        const d = parsePlattsDate('05/03/2026 10:00:00 UTC');
        expect(d).not.toBeNull();
        expect(d.getUTCMonth()).toBe(4); // May = 4 (zero-indexed)
        expect(d.getUTCDate()).toBe(3);
    });

    it('honors an explicit DMY hint for ambiguous dates', () => {
        // detectDateFormat() can pin a grid to DD/MM when an unambiguous sibling row
        // (day >12) proves the format. The hint must override the MM/DD default.
        const d = parsePlattsDate('05/03/2026 10:00:00 UTC', 'DMY');
        expect(d).not.toBeNull();
        expect(d.getUTCMonth()).toBe(2); // March = 2
        expect(d.getUTCDate()).toBe(5);
    });

    it('ignores the hint when a component >12 disambiguates', () => {
        // "05/16/2026" can only be MM/DD (16>12) regardless of any hint.
        const d = parsePlattsDate('05/16/2026 10:00:00 UTC', 'DMY');
        expect(d).not.toBeNull();
        expect(d.getUTCMonth()).toBe(4); // May = 4
        expect(d.getUTCDate()).toBe(16);
    });

    it('returns null for unparseable input', () => {
        expect(parsePlattsDate('')).toBeNull();
        expect(parsePlattsDate('not a date')).toBeNull();
        expect(parsePlattsDate(null)).toBeNull();
    });
});

describe('detectDateFormat', () => {
    it('returns DMY when a first component >12 appears', () => {
        expect(detectDateFormat(['06/11/2026 10:00:00 UTC', '14/06/2026 10:00:00 UTC'])).toBe('DMY');
    });

    it('returns MDY when a second component >12 appears', () => {
        expect(detectDateFormat(['06/11/2026 10:00:00 UTC', '06/20/2026 10:00:00 UTC'])).toBe('MDY');
    });

    it('returns null when every sample is ambiguous (day and month ≤12)', () => {
        // The exact case that broke IODEX: all rows in a grid dated 01–12.
        expect(detectDateFormat(['06/11/2026 15:10:14 UTC', '10/06/2026 14:22:46 UTC'])).toBeNull();
    });

    it('returns null for empty or missing input', () => {
        expect(detectDateFormat([])).toBeNull();
        expect(detectDateFormat(null)).toBeNull();
        expect(detectDateFormat([null, '', 'garbage'])).toBeNull();
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
