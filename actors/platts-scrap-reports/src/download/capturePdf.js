import { promises as fs } from 'node:fs';

import { log } from 'crawlee';

import { ACTIONS_COL_ID, DOWNLOAD_PDF_SELECTORS } from '../grid/columns.js';

const MAGIC_NUMBERS = [
    { header: '25504446', label: 'PDF' },
    { header: '504b0304', label: 'ZIP/Office container (xlsx, pptx, docx)' },
    { header: 'd0cf11e0', label: 'legacy Office document (xls, ppt, doc)' },
];

/**
 * Name the format of a downloaded buffer from its magic number.
 * Used so a rejected download logs "xlsx" instead of a raw hex header —
 * roughly half of Platts' Research Reports are spreadsheets or slide decks.
 */
export function describeFileFormat(buffer) {
    if (!buffer || buffer.length === 0) return 'empty file (0 bytes)';
    const header = buffer.subarray(0, 4).toString('hex');
    const known = MAGIC_NUMBERS.find((m) => m.header === header);
    return known ? known.label : `unknown format (header bytes: ${header})`;
}

/**
 * Click the download trigger on the row at `rowIndex` and return the PDF as a Buffer.
 *
 * The actions cell lives in AG-Grid's pinned-right container, but it is still a
 * descendant of a `.ag-row` carrying the same row-index, so a plain descendant
 * selector reaches it. The trigger is a div[role="button"][aria-label="Download PDF"],
 * not a <button> — matching the aria-label is what keeps us off the neighbouring
 * "Add to Bookmarks" and "Archive" icons.
 *
 * Throws on missing trigger, timeout, empty download, or non-PDF content.
 */
export async function capturePdf(page, rowIndex, timeoutMs = 60000) {
    const actionsCell = page.locator(`.ag-row[row-index="${rowIndex}"] .ag-cell[col-id="${ACTIONS_COL_ID}"]`);

    let trigger = null;
    for (const selector of DOWNLOAD_PDF_SELECTORS) {
        const candidates = actionsCell.locator(selector);
        if ((await candidates.count()) > 0) {
            trigger = candidates.first();
            break;
        }
    }
    if (!trigger) {
        throw new Error(
            `No download trigger in the "${ACTIONS_COL_ID}" cell of row-index=${rowIndex} `
                + `(tried: ${DOWNLOAD_PDF_SELECTORS.join(', ')})`,
        );
    }

    log.info(`Clicking download trigger for row-index=${rowIndex}`);

    const downloadPromise = page.waitForEvent('download', { timeout: timeoutMs });
    await trigger.click();
    const download = await downloadPromise;

    const p = await download.path();
    if (!p) throw new Error('Download saved with no local path');
    const buf = await fs.readFile(p);

    const format = describeFileFormat(buf);
    if (format !== 'PDF') {
        throw new Error(`Downloaded file is ${format}, not a PDF (suggested name: ${download.suggestedFilename()})`);
    }

    log.info(`Captured PDF (${buf.length} bytes, suggested name: ${download.suggestedFilename()})`);
    return buf;
}
