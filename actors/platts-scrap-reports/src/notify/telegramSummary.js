import { log } from 'crawlee';
import fetch from 'node-fetch';

const TG_API = 'https://api.telegram.org/bot';

function escapeMd(s) {
    if (!s) return '';
    return String(s).replace(/([_*`\[\]])/g, '\\$1');
}

/**
 * Build the summary text listing all downloaded reports.
 * @param {string} dateLabel - e.g. "16/04/2026"
 * @param {Array<{reportName, frequency, id}>} reports - downloaded reports with DB id
 */
function buildSummaryText(dateLabel, reports) {
    const lines = reports.map((r) => `• ${escapeMd(r.reportName)} (${escapeMd(r.frequency || '—')})`);
    return (
        `📊 *Platts Reports — ${escapeMd(dateLabel)}*\n\n` +
        `${reports.length} relatório(s) novo(s):\n` +
        lines.join('\n')
    );
}

/**
 * Build inline keyboard: 1 button per report, 1 per row.
 * Callback data: report_dl:<uuid>
 */
function buildKeyboard(reports) {
    return {
        inline_keyboard: reports.map((r) => [
            {
                text: `📥 ${r.reportName}`,
                callback_data: `report_dl:${r.id}`,
            },
        ]),
    };
}

/**
 * Send a single summary message with download buttons.
 * Returns the Telegram message_id (for updating later if needed).
 */
export async function sendReportsSummary(botToken, chatId, dateLabel, reports) {
    if (!botToken) throw new Error('TELEGRAM_BOT_TOKEN required');
    if (!chatId) throw new Error('chatId required');
    if (!reports || reports.length === 0) {
        log.info('No reports to summarize, skipping Telegram.');
        return null;
    }

    const text = buildSummaryText(dateLabel, reports);
    const reply_markup = buildKeyboard(reports);

    const url = `${TG_API}${botToken}/sendMessage`;
    const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            chat_id: String(chatId),
            text,
            parse_mode: 'Markdown',
            reply_markup,
        }),
    });
    if (!res.ok) {
        const body = await res.text();
        throw new Error(`Telegram sendMessage failed: ${res.status} ${body}`);
    }
    const json = await res.json();
    const messageId = json.result?.message_id;
    log.info(`Telegram summary sent (message_id=${messageId}, ${reports.length} buttons)`);
    return messageId;
}
