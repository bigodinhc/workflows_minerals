/**
 * Extrai tabelas do corpo do artigo.
 * Retorna array de { headers, rows, caption? }.
 * Se a tabela não tem <thead>, a primeira <tr> é tratada como header.
 */
export async function extractTables(page) {
    return page.evaluate(() => {
        const results = [];
        const tables = document.querySelectorAll(
            'article table, .newsSection-body table, .platts-newsSection-article table, .readingpane-details table',
        );

        tables.forEach((t) => {
            const headerCells = [...t.querySelectorAll('thead th')];
            const bodyRowEls = [...t.querySelectorAll('tbody tr')];
            const allRowEls = bodyRowEls.length ? bodyRowEls : [...t.querySelectorAll('tr')];

            const hasThead = headerCells.length > 0;
            const headers = hasThead
                ? headerCells.map((th) => (th.innerText || '').trim())
                : [...(allRowEls[0]?.querySelectorAll('th, td') || [])].map((c) => (c.innerText || '').trim());

            const rowsSrc = hasThead ? allRowEls : allRowEls.slice(1);
            const rows = rowsSrc.map((tr) =>
                [...tr.querySelectorAll('td, th')].map((c) => (c.innerText || '').trim()),
            );

            const caption = t.querySelector('caption')?.innerText?.trim() || null;

            if (headers.length || rows.length) {
                results.push({ headers, rows, caption });
            }
        });

        return results;
    });
}
