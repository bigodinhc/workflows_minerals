import { log } from 'crawlee';
import fetch from 'node-fetch';

const TG_API = 'https://api.telegram.org/bot';

function escapeMd(s) {
    if (!s) return '';
    return String(s).replace(/([_*`\[\]])/g, '\\$1');
}

export function buildCaption(row) {
    const name = escapeMd(row.reportName);
    const cover = escapeMd(row.coverDate || '—');
    const pub = escapeMd(row.publishedDate || '—');
    const freq = escapeMd(row.frequency || '—');
    return `📊 *${name}*\nCobertura: ${cover}\nPublicado: ${pub}\nFrequência: ${freq}`;
}

/**
 * Send a PDF buffer as a Telegram document with a Markdown caption.
 * Throws on non-2xx response.
 */
export async function sendPdfDocument(botToken, chatId, pdfBuffer, filename, caption) {
    if (!botToken) throw new Error('TELEGRAM_BOT_TOKEN required');
    if (!chatId) throw new Error('chatId required');

    const form = new FormData();
    form.append('chat_id', String(chatId));
    form.append('caption', caption);
    form.append('parse_mode', 'Markdown');
    form.append('document', new Blob([pdfBuffer], { type: 'application/pdf' }), filename);

    const url = `${TG_API}${botToken}/sendDocument`;
    const res = await fetch(url, { method: 'POST', body: form });
    if (!res.ok) {
        const text = await res.text();
        throw new Error(`Telegram sendDocument failed: ${res.status} ${text}`);
    }
    const json = await res.json();
    log.info(`💬 Telegram document sent (message_id=${json.result?.message_id})`);
    return json;
}
