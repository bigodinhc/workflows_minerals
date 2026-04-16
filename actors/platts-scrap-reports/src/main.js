import { Actor } from 'apify';
import { log } from 'crawlee';
import { chromium } from 'playwright';

import { loginPlatts } from './auth/login.js';
import { capturePdf } from './download/capturePdf.js';
import { applyExcludeFilter } from './filters/applyFilters.js';
import { extractRows } from './grid/extractRows.js';
import { navigateGrid } from './grid/navigateGrid.js';
import { sendReportsSummary } from './notify/telegramSummary.js';
import { isAlreadyStored, setTelegramMessageId, uploadPdf } from './persist/supabaseUpload.js';
import { datePartsFromIso, parsePublishedDate } from './util/dates.js';
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
    telegramChatId,
} = input;

const TG_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const TG_CHAT = telegramChatId || process.env.TELEGRAM_CHAT_ID;
const SB_URL = process.env.SUPABASE_URL;
const SB_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY;

if (!username || !password) {
    await Actor.fail('username and password are required');
}
if (!dryRun && !SB_URL) {
    await Actor.fail('SUPABASE_URL is required when dryRun=false');
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

    // Accumulate downloaded reports across all reportTypes for the summary message
    const allDownloaded = [];

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
        log.info(`${reportType}: ${rows.length} total, ${filtered.length} after filter (cap ${maxReportsPerType})`);

        for (const row of filtered) {
            const slug = slugify(row.reportName);
            const dateKey = parsePublishedDate(row.coverDate);
            if (!slug || !dateKey) {
                summary.errors.push({ stage: 'parse-row', reportName: row.reportName, message: 'missing slug or dateKey' });
                summary.type = 'partial';
                continue;
            }

            // Dedup via Supabase (or skip in dryRun since we don't connect)
            if (!dryRun && !forceRedownload && (await isAlreadyStored(slug, dateKey))) {
                summary.skipped.push({ slug, dateKey, reason: 'already-exists' });
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
            const reportTypeSlug = slugify(reportType);
            const storagePath = `${reportTypeSlug}/${parts.year}/${parts.month}/${filename}`;

            if (dryRun) {
                summary.would_download.push({ slug, dateKey, storagePath, sizeBytes: pdfBuffer.length });
                continue;
            }

            let uploadResult;
            try {
                uploadResult = await uploadPdf(pdfBuffer, {
                    storagePath,
                    metadata: {
                        slug,
                        dateKey,
                        reportName: row.reportName,
                        reportType,
                        frequency: row.frequency,
                        coverDate: row.coverDate,
                        publishedDate: row.publishedDate,
                    },
                });
            } catch (e) {
                log.warning(`Supabase upload failed for ${filename}: ${e.message}`);
                summary.errors.push({ stage: 'supabase-upload', reportName: row.reportName, message: e.message });
                summary.type = 'partial';
                continue;
            }

            allDownloaded.push({
                id: uploadResult.id,
                slug,
                dateKey,
                storagePath,
                reportName: row.reportName,
                frequency: row.frequency,
            });
            summary.downloaded.push({ slug, dateKey, storagePath, supabaseId: uploadResult.id });
        }
    }

    // Send 1 Telegram summary with download buttons (after all report types processed)
    if (!dryRun && allDownloaded.length > 0) {
        const todayLabel = new Date().toLocaleDateString('pt-BR', { timeZone: 'America/Sao_Paulo' });
        try {
            const messageId = await sendReportsSummary(TG_TOKEN, TG_CHAT, todayLabel, allDownloaded);
            // Update telegram_message_id on all downloaded reports
            for (const report of allDownloaded) {
                await setTelegramMessageId(report.id, messageId);
            }
        } catch (e) {
            log.warning(`Telegram summary failed: ${e.message}`);
            summary.errors.push({ stage: 'telegram-summary', message: e.message });
            summary.type = 'partial';
        }
    }

    await Actor.pushData(summary);
}

try {
    await run();
} finally {
    await ctx.close();
    await browser.close();
    await Actor.exit();
}
