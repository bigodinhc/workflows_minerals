import { Actor } from 'apify';
import { log } from 'crawlee';
import { chromium } from 'playwright';

import { loginPlatts } from './auth/login.js';
import { capturePdf } from './download/capturePdf.js';
import { applyExcludeFilter } from './filters/applyFilters.js';
import { extractRows } from './grid/extractRows.js';
import { navigateGrid } from './grid/navigateGrid.js';
import { buildCaption,sendPdfDocument } from './notify/telegramSend.js';
import { uploadPdf } from './storage/gdriveUpload.js';
import { closeRedis,isSeen, markSeen } from './storage/redisDedup.js';
import { datePartsFromIso,parsePublishedDate } from './util/dates.js';
import { slugify } from './util/slug.js';

await Actor.init();

const input = (await Actor.getInput()) ?? {};
const {
    username,
    password,
    reportTypes = ['Market Reports', 'Research Reports'],
    excludeReportNames,
    maxReportsPerType = 50,
    dryRun = false,
    forceRedownload = false,
    gdriveFolderId,
    telegramChatId,
} = input;

const TG_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const TG_CHAT = telegramChatId || process.env.TELEGRAM_CHAT_ID;

if (!username || !password) {
    await Actor.fail('username and password are required');
}
if (!gdriveFolderId && !dryRun) {
    await Actor.fail('gdriveFolderId is required (set in input or .env)');
}
if (!dryRun && (!TG_TOKEN || !TG_CHAT)) {
    await Actor.fail('TELEGRAM_BOT_TOKEN and chat id required when dryRun=false');
}

const summary = {
    type: 'success',
    reportTypes,
    downloaded: [],
    skipped: [],
    errors: [],
    would_download: [],
};

const browser = await chromium.launch({
    headless: process.env.HEADLESS !== 'false',
    slowMo: process.env.SLOW_MO ? parseInt(process.env.SLOW_MO, 10) : 0,
    channel: 'chrome',
    args: ['--no-sandbox'],
});
const ctx = await browser.newContext({ acceptDownloads: true });
const page = await ctx.newPage();
const pageLog = { info: (m) => log.info(m), warn: (m) => log.warning(m), error: (m) => log.error(m) };

async function run() {
    const loginResult = await loginPlatts(page, username, password, pageLog);
    if (!loginResult.ok) {
        try {
            await page.screenshot({ path: 'login-failure.png', fullPage: true });
            log.warning('Saved login-failure.png');
        } catch (_) { /* ignore */ }
        summary.type = 'error';
        summary.errors.push({ stage: 'login', message: loginResult.reason || 'unknown', detail: loginResult.error });
        await Actor.pushData(summary);
        return;
    }

    for (const reportType of reportTypes) {
        try {
            await navigateGrid(page, reportType);
        } catch (e) {
            log.warning(`Skipping "${reportType}": ${e.message}`);
            summary.errors.push({ stage: 'grid-load', reportName: reportType, message: e.message });
            summary.type = 'partial';
            continue;
        }

        const rows = await extractRows(page);
        const filtered = applyExcludeFilter(rows, excludeReportNames).slice(0, maxReportsPerType);
        log.info(`📊 ${reportType}: ${rows.length} total, ${filtered.length} after filter (cap ${maxReportsPerType})`);

        for (const row of filtered) {
            const slug = slugify(row.reportName);
            const dateKey = parsePublishedDate(row.publishedDate);
            if (!slug || !dateKey) {
                summary.errors.push({ stage: 'parse-row', reportName: row.reportName, message: 'missing slug or dateKey' });
                summary.type = 'partial';
                continue;
            }

            if (!forceRedownload && (await isSeen(slug, dateKey))) {
                summary.skipped.push({ slug, dateKey, reason: 'already-seen' });
                continue;
            }

            let pdfBuffer;
            try {
                pdfBuffer = await capturePdf(page, row.rowIndex);
            } catch (e) {
                summary.errors.push({ stage: 'download', reportName: row.reportName, message: e.message });
                summary.type = 'partial';
                continue;
            }

            const parts = datePartsFromIso(dateKey);
            const filename = `${dateKey}_${slug}.pdf`;
            const drivePath = [reportType, parts.year, parts.month];

            if (dryRun) {
                summary.would_download.push({ slug, dateKey, drivePath: `${drivePath.join('/')}/${filename}`, sizeBytes: pdfBuffer.length });
                continue;
            }

            let driveFileId;
            try {
                const upload = await uploadPdf(pdfBuffer, { rootFolderId: gdriveFolderId, pathParts: drivePath, filename });
                driveFileId = upload.fileId;
            } catch (e) {
                summary.errors.push({ stage: 'drive-upload', reportName: row.reportName, message: e.message });
                summary.type = 'partial';
                continue;
            }

            try {
                await sendPdfDocument(TG_TOKEN, TG_CHAT, pdfBuffer, filename, buildCaption(row));
            } catch (e) {
                log.warning(`Telegram send failed for ${filename}: ${e.message}`);
                summary.errors.push({ stage: 'telegram', reportName: row.reportName, message: e.message });
                // Still mark seen — PDF is in Drive, re-sending later is worse than silence
            }

            await markSeen(slug, dateKey);
            summary.downloaded.push({ slug, dateKey, drivePath: `${drivePath.join('/')}/${filename}`, driveFileId });
        }
    }

    await Actor.pushData(summary);
}

try {
    await run();
} finally {
    await closeRedis();
    await ctx.close();
    await browser.close();
    await Actor.exit();
}
