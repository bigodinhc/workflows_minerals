import { log } from 'crawlee';

/**
 * Extract metadata for every data row in the AG-Grid.
 * Returns objects with { reportName, reportTitle, frequency, coverDate, publishedDate, rowIndex }.
 * `rowIndex` is AG-Grid's `row-index` attr (starts at 0) — used later by capturePdf to re-locate the row.
 */
export async function extractRows(page) {
    // Query uniquely by row-index so we de-dupe across pinned containers (AG-Grid
    // may mirror the same row in left/center/right containers — same row-index).
    const rows = await page.evaluate(() => {
        const cellText = (rowIndex, colId) => {
            const cell = document.querySelector(`.ag-row[row-index="${rowIndex}"] .ag-cell[col-id="${colId}"]`);
            return cell ? cell.innerText.trim() : '';
        };
        // Collect unique row-index values present on data rows
        const indices = new Set();
        document.querySelectorAll('.ag-row').forEach((r) => {
            const idx = r.getAttribute('row-index');
            if (idx !== null) indices.add(idx);
        });
        const sorted = Array.from(indices).map(Number).sort((a, b) => a - b);
        return sorted.map((i) => ({
            rowIndex: i,
            reportName: cellText(i, 'reportName'),
            reportTitle: cellText(i, 'reportTitle'),
            frequency: cellText(i, 'frequency'),
            coverDate: cellText(i, 'formattedCoverDate'),
            publishedDate: cellText(i, 'publisheddate'),
        }));
    });
    log.info(`Extracted ${rows.length} rows`);
    return rows.filter((r) => r.reportName); // drop rows with no name (shouldn't happen, defensive)
}
