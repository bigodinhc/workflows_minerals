import { describe, expect, it, vi } from 'vitest';

import { createPlattsApi, playwrightRequest } from '../src/api/plattsApi.js';

const okJson = (body) => ({ status: 200, json: async () => body, body: async () => Buffer.from('') });
const okPdf = (buf) => ({ status: 200, json: async () => ({}), body: async () => buf });
const status = (code) => ({ status: code, json: async () => ({}), body: async () => Buffer.from('') });

function makeAuth(headers = { authorization: 'Bearer t1', appkey: 'realtime' }) {
    const state = { headers, refreshCount: 0 };
    return {
        headers: () => state.headers,
        refresh: async () => { state.refreshCount += 1; state.headers = { ...state.headers, authorization: 'Bearer t2' }; },
        state,
    };
}

describe('searchArchive', () => {
    it('faz POST no endpoint do blendedsearch com os headers de auth', async () => {
        const post = vi.fn(async () => okJson({ Items: [], TotalPages: 0 }));
        const api = createPlattsApi({ request: { post, get: vi.fn() }, auth: makeAuth(), sleep: async () => {} });

        await api.searchArchive({ page: 1 });

        expect(post).toHaveBeenCalledTimes(1);
        const [url, opts] = post.mock.calls[0];
        expect(url).toBe('https://api.platts.com/platts-platform/content-bff/v4/search/blendedsearch');
        expect(opts.headers.authorization).toBe('Bearer t1');
        expect(opts.headers.appkey).toBe('realtime');
        expect(opts.data).toEqual({ page: 1 });
    });
});

describe('fetchPdf', () => {
    it('faz GET no stream do Id e devolve o Buffer', async () => {
        const pdf = Buffer.from('%PDF-1.7 conteudo');
        const get = vi.fn(async () => okPdf(pdf));
        const api = createPlattsApi({ request: { post: vi.fn(), get }, auth: makeAuth(), sleep: async () => {} });

        const out = await api.fetchPdf('abc-123');

        expect(get.mock.calls[0][0]).toBe('https://api.platts.com/platts-platform/content-bff/v2/search/stream/abc-123');
        expect(out).toEqual(pdf);
    });
});

describe('renovação de token', () => {
    it('recaptura e retenta uma vez ao receber 401', async () => {
        const auth = makeAuth();
        const post = vi.fn()
            .mockResolvedValueOnce(status(401))
            .mockResolvedValueOnce(okJson({ Items: [] }));
        const api = createPlattsApi({ request: { post, get: vi.fn() }, auth, sleep: async () => {} });

        await api.searchArchive({ page: 1 });

        expect(auth.state.refreshCount).toBe(1);
        expect(post).toHaveBeenCalledTimes(2);
        expect(post.mock.calls[1][1].headers.authorization).toBe('Bearer t2');
    });

    it('desiste quando o 401 persiste depois da recaptura', async () => {
        const auth = makeAuth();
        const post = vi.fn(async () => status(401));
        const api = createPlattsApi({ request: { post, get: vi.fn() }, auth, sleep: async () => {} });

        await expect(api.searchArchive({ page: 1 })).rejects.toThrow(/auth/i);
        expect(auth.state.refreshCount).toBe(1);
    });

    it('trata 403 como 401', async () => {
        const auth = makeAuth();
        const post = vi.fn().mockResolvedValueOnce(status(403)).mockResolvedValueOnce(okJson({ Items: [] }));
        const api = createPlattsApi({ request: { post, get: vi.fn() }, auth, sleep: async () => {} });

        await api.searchArchive({ page: 1 });
        expect(auth.state.refreshCount).toBe(1);
    });
});

describe('playwrightRequest', () => {
    it('achata os métodos status() e body() do Playwright em campos simples', async () => {
        const ctx = { request: {
            post: async () => ({ status: () => 200, json: async () => ({ ok: 1 }), body: async () => Buffer.from('x') }),
            get: async () => ({ status: () => 404, json: async () => ({}), body: async () => Buffer.from('') }),
        } };
        const request = playwrightRequest(ctx);

        const posted = await request.post('u', { headers: {}, data: {} });
        expect(posted.status).toBe(200);
        expect(await posted.json()).toEqual({ ok: 1 });

        const got = await request.get('u', { headers: {} });
        expect(got.status).toBe(404);
    });
});

describe('backoff', () => {
    it('retenta 429 com espera crescente', async () => {
        const waits = [];
        const post = vi.fn()
            .mockResolvedValueOnce(status(429))
            .mockResolvedValueOnce(status(429))
            .mockResolvedValueOnce(okJson({ Items: [] }));
        const api = createPlattsApi({
            request: { post, get: vi.fn() },
            auth: makeAuth(),
            sleep: async (ms) => { waits.push(ms); },
        });

        await api.searchArchive({ page: 1 });

        expect(post).toHaveBeenCalledTimes(3);
        expect(waits[1]).toBeGreaterThan(waits[0]);
    });

    it('desiste depois de maxRetries em 5xx', async () => {
        const post = vi.fn(async () => status(503));
        const api = createPlattsApi({
            request: { post, get: vi.fn() }, auth: makeAuth(), sleep: async () => {}, maxRetries: 3,
        });

        await expect(api.searchArchive({ page: 1 })).rejects.toThrow(/503/);
        expect(post).toHaveBeenCalledTimes(3);
    });

    it('não retenta um 400 — é erro nosso, não do servidor', async () => {
        const post = vi.fn(async () => status(400));
        const api = createPlattsApi({ request: { post, get: vi.fn() }, auth: makeAuth(), sleep: async () => {} });

        await expect(api.searchArchive({ page: 1 })).rejects.toThrow(/400/);
        expect(post).toHaveBeenCalledTimes(1);
    });
});
