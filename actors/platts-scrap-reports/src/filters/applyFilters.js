export const DEFAULT_EXCLUDES = [
    '- Portuguese',
    '(Português)',
    '(Portugues)',
    '(Español)',
    'Perspectiva Global',
    'Panorama Semanal',
];

/**
 * Filter rows whose reportName contains any excluded substring (case-insensitive).
 * @param {Array<{reportName: string}>} rows
 * @param {string[]} excludes
 */
export function applyExcludeFilter(rows, excludes) {
    if (!Array.isArray(rows) || rows.length === 0) return [];
    if (!Array.isArray(excludes) || excludes.length === 0) return rows.slice();
    const lowerExcludes = excludes.map((s) => s.toLowerCase());
    return rows.filter((row) => {
        const name = (row.reportName || '').toLowerCase();
        return !lowerExcludes.some((pat) => name.includes(pat));
    });
}
