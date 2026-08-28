import { log } from 'crawlee';

import { REPORT_COLUMNS } from './columns.js';
import { scrapeRowsFromDom } from './scrapeRows.js';

/**
 * Extract metadata for every data row in the AG-Grid.
 * Returns objects with { reportName, reportTitle, frequency, coverDate, publishedDate, rowIndex }.
 * `rowIndex` is AG-Grid's `row-index` attr (starts at 0) — used later by capturePdf to re-locate the row.
 *
 * Throws if rows render but none carry a report name: that means Platts renamed
 * the columns again, and a silent [] would look identical to "nothing published
 * today". Failing here surfaces as cron_crashed + a red run.
 */
export async function extractRows(page) {
    const { rows, colIdsSeen } = await page.evaluate(scrapeRowsFromDom, { columns: REPORT_COLUMNS });
    const named = rows.filter((r) => r.reportName);

    log.info(`Extracted ${rows.length} rows (${named.length} named)`);

    if (rows.length > 0 && named.length === 0) {
        throw new Error(
            `Grid layout changed: ${rows.length} row(s) rendered but none matched `
                + `col-id "${REPORT_COLUMNS.reportName}". Col-ids present: ${colIdsSeen.join(', ') || '(none)'}`,
        );
    }

    return named;
}
