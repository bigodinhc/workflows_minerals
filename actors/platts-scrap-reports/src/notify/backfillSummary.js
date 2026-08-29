import { log } from 'crawlee';
import fetch from 'node-fetch';

const TG_API = 'https://api.telegram.org/bot';

/**
 * Uma única mensagem ao final do backfill.
 *
 * Sem botão por relatório: novecentos botões inline estouram o limite do
 * Telegram e não serviriam para nada. Sem marcação Markdown, porque o texto
 * carrega nomes de publicação e mensagens de erro cruas — o mesmo 400 que
 * derrubou os alertas do _MainChatSink por um mês.
 */
export function buildBackfillSummaryText(summary) {
    const errorsByStage = summary.errors.reduce((acc, e) => {
        acc[e.stage] = (acc[e.stage] || 0) + 1;
        return acc;
    }, {});

    const errorLine = Object.keys(errorsByStage).length === 0
        ? 'sem erros'
        : Object.entries(errorsByStage).map(([stage, n]) => `${stage}: ${n}`).join(', ');

    const minutes = Math.round(summary.durationMs / 60000);

    const lines = [
        'Backfill Platts concluido',
        '',
        `Baixados: ${summary.downloaded.length}`,
        `Pulados (ja existiam): ${summary.skipped.length}`,
        `Erros: ${errorLine}`,
        `Duracao: ${minutes} min`,
        '',
        'Por publicacao:',
        ...summary.publications.map((p) => `- ${p.publication}: ${p.processed}/${p.totalRecords}`),
    ];

    return lines.join('\n');
}

export async function sendBackfillSummary(botToken, chatId, summary) {
    if (!botToken || !chatId) {
        log.warning('Telegram nao configurado; pulando resumo do backfill.');
        return null;
    }

    const response = await fetch(`${TG_API}${botToken}/sendMessage`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            chat_id: String(chatId),
            text: buildBackfillSummaryText(summary),
        }),
    });

    const payload = await response.json();
    if (!payload.ok) {
        throw new Error(`Telegram rejected the backfill summary: ${payload.description}`);
    }
    return payload.result.message_id;
}
