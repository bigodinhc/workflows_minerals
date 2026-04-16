const PT_MONTHS = {
    'jan': '01', 'fev': '02', 'mar': '03', 'abr': '04', 'mai': '05', 'jun': '06',
    'jul': '07', 'ago': '08', 'set': '09', 'out': '10', 'nov': '11', 'dez': '12',
};
const EN_MONTHS = {
    'jan': '01', 'feb': '02', 'mar': '03', 'apr': '04', 'may': '05', 'jun': '06',
    'jul': '07', 'aug': '08', 'sep': '09', 'oct': '10', 'nov': '11', 'dec': '12',
};

export function parsePublishedDate(raw) {
    if (!raw || typeof raw !== 'string') return null;
    const s = raw.trim();

    // "DD/MM/YYYY" or "MM/DD/YYYY" with optional time/UTC suffix
    // Disambiguate: if first group > 12 → it's the day (DD/MM). If second > 12 → MM/DD.
    // If both ≤ 12 → default to DD/MM (European convention, Platts default).
    const slash = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})(?:\s|$)/);
    if (slash) {
        let [, a, b, year] = slash;
        const n1 = parseInt(a, 10);
        const n2 = parseInt(b, 10);
        let day, month;
        if (n1 > 12) {
            // n1 must be day (DD/MM/YYYY)
            day = a; month = b;
        } else if (n2 > 12) {
            // n2 must be day (MM/DD/YYYY)
            day = b; month = a;
        } else {
            // Ambiguous — default DD/MM (European)
            day = a; month = b;
        }
        return `${year}-${month.padStart(2, '0')}-${day.padStart(2, '0')}`;
    }

    // "DD <month-abbrev>. YYYY" or "DD <month-abbrev> YYYY"
    const word = s.match(/^(\d{1,2})\s+([a-z]{3})\.?\s+(\d{4})/i);
    if (word) {
        const day = word[1].padStart(2, '0');
        const monAbbr = word[2].toLowerCase();
        const month = PT_MONTHS[monAbbr] || EN_MONTHS[monAbbr];
        if (month) return `${word[3]}-${month}-${day}`;
    }

    return null;
}

export function datePartsFromIso(iso) {
    if (!iso || typeof iso !== 'string') return null;
    const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!m) return null;
    return { year: m[1], month: m[2], day: m[3] };
}
