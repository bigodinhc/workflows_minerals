import { beforeEach, describe, expect, it, vi } from 'vitest';

import { buildBackfillSummaryText, sendBackfillSummary } from '../src/notify/backfillSummary.js';

vi.mock('node-fetch', () => ({ default: vi.fn() }));
const fetch = (await import('node-fetch')).default;

const summary = {
    downloaded: new Array(912).fill({}),
    skipped: new Array(15).fill({}),
    errors: [
        { stage: 'download', message: 'is ZIP/Office container' },
        { stage: 'download', message: 'is ZIP/Office container' },
        { stage: 'supabase-upload', message: 'Storage upload failed' },
    ],
    publications: [
        { publication: 'Steel Price Report', totalRecords: 249, processed: 249 },
        { publication: 'Cement Weekly', totalRecords: 52, processed: 52 },
    ],
    durationMs: 1_500_000,
};

describe('buildBackfillSummaryText', () => {
    it('mostra os totais que importam', () => {
        const text = buildBackfillSummaryText(summary);
        expect(text).toContain('912');
        expect(text).toContain('15');
    });

    it('agrupa os erros por etapa em vez de listar um a um', () => {
        const text = buildBackfillSummaryText(summary);
        expect(text).toContain('download: 2');
        expect(text).toContain('supabase-upload: 1');
    });

    it('mostra a duração em minutos', () => {
        expect(buildBackfillSummaryText(summary)).toContain('25 min');
    });

    // Style check on our own formatting, NOT the safety mechanism — the missing parse_mode is what makes it safe.
    it('não usa marcação Markdown — o texto vai com parse_mode desligado', () => {
        const text = buildBackfillSummaryText(summary);
        expect(text).not.toMatch(/[*_`]/);
    });

    it('lida com um backfill sem nenhum erro', () => {
        const text = buildBackfillSummaryText({ ...summary, errors: [] });
        expect(text).toContain('sem erros');
    });
});

describe('sendBackfillSummary', () => {
    const okResponse = { json: async () => ({ ok: true, result: { message_id: 8053 } }) };

    beforeEach(() => { fetch.mockReset(); });

    it('nao envia parse_mode — e a ausencia dele que impede o 400 do Telegram', async () => {
        fetch.mockResolvedValue(okResponse);
        await sendBackfillSummary('token123', '4242', summary);

        const body = JSON.parse(fetch.mock.calls[0][1].body);
        expect(body).not.toHaveProperty('parse_mode');
        expect(body).not.toHaveProperty('reply_markup');
        expect(body.chat_id).toBe('4242');
        expect(body.text).toContain('Backfill');
    });

    it('devolve o message_id do Telegram', async () => {
        fetch.mockResolvedValue(okResponse);
        expect(await sendBackfillSummary('token123', '4242', summary)).toBe(8053);
    });

    it('lanca quando o Telegram recusa, citando o motivo', async () => {
        fetch.mockResolvedValue({ json: async () => ({ ok: false, description: "can't parse entities" }) });
        await expect(sendBackfillSummary('token123', '4242', summary)).rejects.toThrow(/parse entities/);
    });

    it('nao vaza o token na mensagem de erro', async () => {
        fetch.mockResolvedValue({ json: async () => ({ ok: false, description: 'chat not found' }) });
        const error = await sendBackfillSummary('segredo-do-bot', '4242', summary).catch((e) => e);
        expect(error.message).not.toContain('segredo-do-bot');
    });

    it('pula em silencio sem credenciais, sem chamar o Telegram', async () => {
        expect(await sendBackfillSummary('', '4242', summary)).toBeNull();
        expect(await sendBackfillSummary('token123', '', summary)).toBeNull();
        expect(fetch).not.toHaveBeenCalled();
    });
});
