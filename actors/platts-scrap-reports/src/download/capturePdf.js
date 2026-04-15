import { log } from 'crawlee';
import { promises as fs } from 'node:fs';

/**
 * Click the PDF icon on the row at `rowIndex` and return the downloaded PDF as a Buffer.
 * AG-Grid pinned-right columns render in a separate container, so we locate the actions
 * cell by its row-index + col-id attributes (the row-index is shared across pinned containers).
 *
 * Throws on timeout, empty download, or non-PDF content.
 */
export async function capturePdf(page, rowIndex, timeoutMs = 60000) {
    // Locate the actions cell for this row-index (may be in a pinned-right container)
    const actionsCell = page.locator(`.ag-cell[col-id="actionBookmark"]`).filter({
        has: page.locator(`xpath=ancestor::*[@row-index="${rowIndex}"]`),
    });

    // Within the actions cell, try specific PDF triggers in priority order
    const pdfButton = actionsCell.locator(
        '[aria-label*="PDF" i], [aria-label*="download" i], [title*="PDF" i], [title*="download" i], button, a, [role="button"]',
    ).last();

    const count = await pdfButton.count();
    if (count === 0) {
        throw new Error(`No PDF trigger found in actions cell of row-index=${rowIndex}`);
    }

    log.info(`Clicking PDF trigger for row-index=${rowIndex} (matched ${count} candidates, using last)`);

    const downloadPromise = page.waitForEvent('download', { timeout: timeoutMs });
    await pdfButton.click();
    const download = await downloadPromise;

    const p = await download.path();
    if (!p) throw new Error('Download saved with no local path');
    const buf = await fs.readFile(p);
    if (buf.length === 0) throw new Error('Downloaded PDF is empty (0 bytes)');
    if (buf.subarray(0, 4).toString('utf-8') !== '%PDF') {
        throw new Error(`Downloaded file is not a PDF (header bytes: ${buf.subarray(0, 4).toString('hex')})`);
    }
    log.info(`Captured PDF (${buf.length} bytes, suggested name: ${download.suggestedFilename()})`);
    return buf;
}
