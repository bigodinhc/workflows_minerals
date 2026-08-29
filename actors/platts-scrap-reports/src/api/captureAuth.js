const API_HOST = 'api.platts.com';

/**
 * Checks if a URL belongs to the Platts API by comparing the hostname exactly.
 * Returns false without throwing for malformed URLs.
 */
function isPlattsApi(url) {
    try {
        return new URL(url).hostname === API_HOST;
    } catch {
        return false;
    }
}

/**
 * Escuta o tráfego da página e guarda os headers de autenticação da primeira
 * chamada à API do Platts.
 *
 * O SPA autentica por Bearer JWT, não por cookie, então um fetch nosso não
 * herda a sessão — o token tem que ser copiado de uma requisição real.
 *
 * Devolve um objeto de estado que o listener preenche. Quem chama observa
 * `state.headers`.
 */
export function attachAuthCapture(page) {
    const state = { headers: null };

    page.on('request', (request) => {
        if (!isPlattsApi(request.url())) return;
        const headers = request.headers();
        if (!headers.authorization) return;

        state.headers = {
            authorization: headers.authorization,
            appkey: headers.appkey || 'realtime',
            'x-origin-app': 'Web',
            'x-preferred-language': 'en',
        };
    });

    return state;
}

/** Espera o listener capturar os headers, ou desiste com erro explícito. */
export async function waitForAuth(state, {
    timeoutMs = 30000,
    intervalMs = 250,
    sleep = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); }),
    now = () => Date.now(),
} = {}) {
    const deadline = now() + timeoutMs;

    while (now() < deadline) {
        if (state.headers) return state.headers;
        await sleep(intervalMs);
    }

    throw new Error(
        `Timed out after ${timeoutMs}ms waiting to capture Platts auth headers. `
        + 'The grid page fires an api.platts.com request on load — if none arrived, the session is probably not logged in.',
    );
}
