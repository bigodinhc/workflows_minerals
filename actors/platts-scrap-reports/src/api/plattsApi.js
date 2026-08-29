const BASE = 'https://api.platts.com/platts-platform/content-bff';
const SEARCH_URL = `${BASE}/v4/search/blendedsearch`;
const STREAM_URL = `${BASE}/v2/search/stream`;

const defaultSleep = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

/**
 * Adapta o APIRequestContext do Playwright para a interface que este módulo
 * consome. O Playwright expõe status() e body() como métodos; aqui viram
 * um objeto simples, que é o que os testes conseguem fingir.
 */
export function playwrightRequest(ctx) {
    const wrap = async (response) => ({
        status: response.status(),
        json: () => response.json(),
        body: () => response.body(),
    });
    return {
        post: async (url, { headers, data }) => wrap(await ctx.request.post(url, { headers, data })),
        get: async (url, { headers }) => wrap(await ctx.request.get(url, { headers })),
    };
}

/**
 * Cliente das duas chamadas que o backfill precisa.
 *
 * Renovação de token por reação, não por relógio: um 401 é o sinal para
 * recapturar. Não medimos a validade do JWT, e confiar num palpite traz o
 * modo de falha de "o relógio dizia que faltava tempo e o servidor discordou".
 */
export function createPlattsApi({ request, auth, sleep = defaultSleep, maxRetries = 3 }) {
    async function send(perform) {
        let attempts = 0;
        let refreshed = false;

        for (;;) {
            const response = await perform(auth.headers());

            if (response.status === 401 || response.status === 403) {
                if (refreshed) {
                    throw new Error(`Platts auth rejected after refresh (status ${response.status})`);
                }
                await auth.refresh();
                refreshed = true;
                continue;
            }

            if (response.status === 429 || response.status >= 500) {
                attempts += 1;
                if (attempts >= maxRetries) {
                    throw new Error(`Platts request failed after ${attempts} attempts (status ${response.status})`);
                }
                await sleep(2 ** attempts * 500);
                continue;
            }

            if (response.status >= 400) {
                throw new Error(`Platts request failed (status ${response.status})`);
            }

            return response;
        }
    }

    return {
        async searchArchive(payload) {
            const response = await send((headers) => request.post(SEARCH_URL, {
                headers: { ...headers, 'content-type': 'application/json' },
                data: payload,
            }));
            return response.json();
        },

        async fetchPdf(id) {
            const response = await send((headers) => request.get(`${STREAM_URL}/${id}`, { headers }));
            return response.body();
        },
    };
}
