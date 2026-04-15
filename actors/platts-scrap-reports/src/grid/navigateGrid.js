import { log } from 'crawlee';

const BASE_URL = 'https://core.spglobal.com/#platts/rptsSearch';
const GRID_READY_SELECTOR = '.ag-row';

/**
 * Navigate to the report-type grid and wait for the first data row to render.
 * Throws if the grid never renders within 30s.
 */
export async function navigateGrid(page, reportType) {
    const url = `${BASE_URL}?reportType=${encodeURIComponent(reportType)}`;
    log.info(`Navigating to ${reportType} grid: ${url}`);
    await page.goto(url, { waitUntil: 'networkidle', timeout: 60000 });
    try {
        await page.waitForSelector(GRID_READY_SELECTOR, { timeout: 30000 });
    } catch (e) {
        throw new Error(`Grid did not render for "${reportType}" within 30s: ${e.message}`);
    }
    // Small settle time for AG-Grid to finish rendering all cells in viewport
    await page.waitForTimeout(2000);
    log.info(`Grid ready for ${reportType}`);
}
