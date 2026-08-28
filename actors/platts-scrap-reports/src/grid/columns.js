/**
 * AG-Grid column ids on the Platts reports grid.
 *
 * Platts renamed every col-id on 2026-08-01, which silently zeroed the actor
 * for four weeks. Keeping the ids in one place means the next rename is a
 * one-file change, and `extractRows` fails loudly instead of returning [].
 *
 * Verified against the live grid on 2026-08-28.
 */
export const REPORT_COLUMNS = {
    reportName: 'Title',
    reportTitle: 'ReportTitle',
    frequency: 'Frequency',
    coverDate: '__coverDateOriginal',
    publishedDate: 'UpdatedDate',
};

/** Column holding the per-row action icons (bookmark / archive / download). */
export const ACTIONS_COL_ID = 'Action';

/**
 * The download trigger inside the actions cell. It is a div[role="button"],
 * not a <button>, so match on the aria-label rather than on the tag.
 */
export const DOWNLOAD_PDF_SELECTORS = ['[aria-label="Download PDF"]', '[aria-label*="download" i]'];
