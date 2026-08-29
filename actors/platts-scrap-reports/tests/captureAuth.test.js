import { describe, expect, it, vi } from 'vitest';

import { attachAuthCapture, waitForAuth } from '../src/api/captureAuth.js';

/** Página falsa: guarda o handler de 'request' e deixa o teste dispará-lo. */
function fakePage() {
    const handlers = {};
    return {
        on: (event, fn) => { handlers[event] = fn; },
        fire: (req) => handlers.request(req),
    };
}

const req = (url, headers) => ({ url: () => url, headers: () => headers });

describe('attachAuthCapture', () => {
    it('captura authorization e appkey de uma chamada à api.platts.com', () => {
        const page = fakePage();
        const state = attachAuthCapture(page);

        page.fire(req('https://api.platts.com/platts-platform/content-bff/v4/search/blendedsearch', {
            authorization: 'Bearer eyJraWQ.abc', appkey: 'realtime',
        }));

        expect(state.headers.authorization).toBe('Bearer eyJraWQ.abc');
        expect(state.headers.appkey).toBe('realtime');
    });

    it('ignora tráfego que não é da api.platts.com', () => {
        const page = fakePage();
        const state = attachAuthCapture(page);

        page.fire(req('https://events.launchdarkly.com/events/bulk/x', { authorization: 'Bearer outro' }));

        expect(state.headers).toBeNull();
    });

    it('ignora chamada da api sem authorization', () => {
        const page = fakePage();
        const state = attachAuthCapture(page);

        page.fire(req('https://api.platts.com/qualquer', { 'x-origin-app': 'Web' }));

        expect(state.headers).toBeNull();
    });

    it('mantém o token mais recente quando chega outro', () => {
        const page = fakePage();
        const state = attachAuthCapture(page);

        page.fire(req('https://api.platts.com/a', { authorization: 'Bearer velho', appkey: 'realtime' }));
        page.fire(req('https://api.platts.com/b', { authorization: 'Bearer novo', appkey: 'realtime' }));

        expect(state.headers.authorization).toBe('Bearer novo');
    });

    it('assume appkey realtime quando a requisição não traz', () => {
        const page = fakePage();
        const state = attachAuthCapture(page);

        page.fire(req('https://api.platts.com/a', { authorization: 'Bearer x' }));

        expect(state.headers.appkey).toBe('realtime');
    });
});

describe('waitForAuth', () => {
    it('devolve os headers assim que aparecem', async () => {
        const state = { headers: null };
        const sleep = vi.fn(async () => { state.headers = { authorization: 'Bearer x' }; });

        const headers = await waitForAuth(state, { sleep, timeoutMs: 1000, intervalMs: 10 });

        expect(headers.authorization).toBe('Bearer x');
    });

    it('devolve imediatamente se já foram capturados', async () => {
        const sleep = vi.fn();
        const headers = await waitForAuth({ headers: { authorization: 'Bearer pronto' } }, { sleep });

        expect(headers.authorization).toBe('Bearer pronto');
        expect(sleep).not.toHaveBeenCalled();
    });

    it('lança ao estourar o prazo, dizendo o que faltou', async () => {
        let clock = 0;
        const state = { headers: null };

        await expect(waitForAuth(state, {
            timeoutMs: 100, intervalMs: 10,
            sleep: async (ms) => { clock += ms; },
            now: () => clock,
        })).rejects.toThrow(/auth/i);
    });
});
