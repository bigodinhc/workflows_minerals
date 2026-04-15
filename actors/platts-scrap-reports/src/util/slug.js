/**
 * Convert a report name to a URL/filename-safe slug.
 *
 *   "SBB Steel Markets Daily" → "sbb-steel-markets-daily"
 *   "Global Market Outlook (Português)" → "global-market-outlook-portugues"
 */
export function slugify(input) {
    if (!input || typeof input !== 'string') return '';
    return input
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '')
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '');
}
