/**
 * Read every data row out of the AG-Grid.
 *
 * This function is serialized and executed inside the browser by
 * `page.evaluate`, so it must stay self-contained: no imports, no closure
 * references, no optional chaining on globals. `doc` is injected only by the
 * tests; in the browser it falls back to the real `document`.
 *
 * @param {{columns: Record<string,string>, doc?: Document}} arg
 * @returns {{rows: Array<Object>, colIdsSeen: string[]}}
 */
export function scrapeRowsFromDom({ columns, doc }) {
    const d = doc || document;

    const cellText = (rowIndex, colId) => {
        const cell = d.querySelector(`.ag-row[row-index="${rowIndex}"] .ag-cell[col-id="${colId}"]`);
        if (!cell) return '';
        // textContent, not innerText: it survives cells the headless layout
        // never paints, and jsdom implements it.
        return (cell.textContent || '').replace(/\s+/g, ' ').trim();
    };

    // AG-Grid mirrors the same row-index across pinned-left / center /
    // pinned-right containers, so collect distinct indices rather than nodes.
    const indices = new Set();
    d.querySelectorAll('.ag-row').forEach((r) => {
        const idx = r.getAttribute('row-index');
        if (idx !== null) indices.add(idx);
    });

    const fields = Object.keys(columns);
    const rows = Array.from(indices)
        .map(Number)
        .sort((a, b) => a - b)
        .map((i) => {
            const row = { rowIndex: i };
            for (const field of fields) row[field] = cellText(i, columns[field]);
            return row;
        });

    const colIdsSeen = Array.from(
        new Set(
            Array.from(d.querySelectorAll('.ag-cell'))
                .map((c) => c.getAttribute('col-id'))
                .filter(Boolean),
        ),
    );

    return { rows, colIdsSeen };
}
