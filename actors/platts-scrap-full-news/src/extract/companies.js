// Canonical list of companies tracked across iron ore / met coal / steel coverage.
// Union of two previously-divergent inline lists in articlePage.js and readingPane.js.
// Used by both extractors to populate metadata.companies — keep alphabetized so PRs
// that add a new player don't churn unrelated diff lines.
//
// IMPORTANT: this list is passed across the page.evaluate boundary (Node → browser),
// so it must remain plain JSON-serializable strings. No regex, no closures.
export const COMPANIES = [
    'Anglo American',
    'ArcelorMittal',
    'Baosteel',
    'Baowu',
    'BHP',
    'Cargill',
    'Cleveland-Cliffs',
    'CMRG',
    'CSN',
    'FMG',
    'Fortescue',
    'JFE',
    'Nippon Steel',
    'Nucor',
    'POSCO',
    'Rio Tinto',
    'Shougang',
    'Tata Steel',
    'Trafigura',
    'US Steel',
    'Vale',
];
