# Backfill dos Market Reports de 2025 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adicionar um modo `backfill` ao actor `platts-scrap-reports` que baixa as edições de 2025 dos Market Reports (~927 PDFs em 8 publicações) via a API do Platts, reaproveitando bucket, tabela e dedup do fluxo diário.

**Architecture:** O botão "Archive" do grid dispara `POST /content-bff/v4/search/blendedsearch` (com `fromDate`/`toDate`/`page`) e o download é `GET /content-bff/v2/search/stream/{Id}`. O backfill chama as duas direto pelo `ctx.request` do Playwright, que só entra em cena para o login e para capturar o `Bearer`. O caminho diário (`navigateGrid`/`extractRows`/`capturePdf`) não é tocado.

**Tech Stack:** Node 22 ESM, Playwright 1.54, Apify SDK 3, Supabase JS 2, vitest 2.

**Spec:** `docs/superpowers/specs/2026-08-28-platts-backfill-2025-design.md`

## Global Constraints

- Todo código novo em ESM (`import`/`export`), sem CommonJS. O `package.json` tem `"type": "module"`.
- Rodar testes de dentro de `actors/platts-scrap-reports` com `npm test` (vitest).
- Lint: `npx eslint <arquivos>`. O repo tem 30 erros pré-existentes em `tests/eventBus.test.js` e `src/util/dates.js` — **não corrigir**, apenas garantir que os arquivos novos passam limpos.
- Imports ordenados alfabeticamente por grupo (`simple-import-sort`); rodar `npx eslint --fix` resolve.
- **Nunca converter `CoverDate` com `new Date()`.** É meia-noite UTC; em UTC-3 o dia volta um. Usar `slice`/regex sobre a string.
- Sem `console.log`: usar o `log` do `crawlee` (`import { log } from 'crawlee'`).
- O caminho diário do actor não pode mudar de comportamento. Nenhuma alteração em `navigateGrid.js`, `extractRows.js`, `scrapeRows.js`, `capturePdf.js`, `columns.js`.
- Endpoints, verbatim:
  - `https://api.platts.com/platts-platform/content-bff/v4/search/blendedsearch`
  - `https://api.platts.com/platts-platform/content-bff/v2/search/stream/{id}`
- Header constante: `appkey: realtime`.

### Refinamentos sobre a spec

Dois pontos que a spec deixou implícitos e este plano fixa:

1. A spec lista 4 módulos novos; este plano usa 7, separando `searchPayload`, `mapLimit` e `backfillSummary` para que cada peça pura tenha teste próprio.
2. A spec diz "falha ao listar uma publicação → segue" **e** "rendimento zero → lança". Isso conflita: um `catch` por publicação engoliria o guard. Resolvido com uma classe `ZeroYieldError` que o laço explicitamente **não** captura.

---

## File Structure

| Arquivo | Responsabilidade |
|---|---|
| `src/api/parseItems.js` | `Items[]` da API → linhas normalizadas; extrai `dateKey` |
| `src/api/searchPayload.js` | monta o corpo do `blendedsearch` e a janela ISO |
| `src/api/plattsApi.js` | executa search e stream; retry, 401, adapter do Playwright |
| `src/api/captureAuth.js` | captura os headers de auth do tráfego da página |
| `src/util/concurrency.js` | `mapLimit` — concorrência limitada |
| `src/notify/backfillSummary.js` | texto e envio do resumo único no Telegram |
| `src/backfill/runBackfill.js` | orquestra publicações, páginas, dedup, upload |
| `src/main.js` | branch de `mode` |
| `.actor/input_schema.json` | campos novos do input |

---

### Task 1: parseItems — da resposta da API para linhas

**Files:**
- Create: `src/api/parseItems.js`
- Create: `tests/fixtures/blendedsearch-response.json`
- Test: `tests/parseItems.test.js`

**Interfaces:**
- Consumes: nada.
- Produces: `parseItems(response) -> Array<{id: string, fileName: string, reportName: string, dateKey: string, frequency: string|null, coverDate: string}>`; `dateKeyFromCoverDate(coverDate: string) -> string|null`; `paginationOf(response) -> {page: number, totalPages: number, totalRecords: number}`.

- [ ] **Step 1: Criar a fixture com dados reais da API**

Criar `tests/fixtures/blendedsearch-response.json`:

```json
{
  "HasTrialRestrictions": false,
  "Items": [
    {
      "Name": "SPR_20260827.pdf",
      "ReportName": "Steel Price Report",
      "ReportType": "MARKET REPORT",
      "MimeType": "application/pdf",
      "PublicationGrouping": [
        { "Id": "8174a051-a9a5-43c7-9817-e8257efec05a", "MimeType": "application/pdf", "Name": "SPR_20260827.pdf" },
        { "Id": "0312a345-8055-4589-a5dd-a729bcf16754", "MimeType": "application/xml", "Name": "SPR_20260827_TopicMerge.xml" }
      ],
      "Frequency": "Daily",
      "Language": "English",
      "PackageShortCode": "SPR",
      "CoverDate": "2026-08-27T00:00:00.000Z",
      "UpdatedDate": "2026-08-27T21:54:13Z"
    },
    {
      "Name": "SPR_20260826.pdf",
      "ReportName": "Steel Price Report",
      "ReportType": "MARKET REPORT",
      "MimeType": "application/pdf",
      "PublicationGrouping": [
        { "Id": "47b02a21-3673-4a5f-aa59-3519fd78a9be", "MimeType": "application/pdf", "Name": "SPR_20260826.pdf" },
        { "Id": "d901850f-bf32-44b9-9de8-a738ca72306a", "MimeType": "application/xml", "Name": "SPR_20260826_TopicMerge.xml" }
      ],
      "Frequency": "Daily",
      "Language": "English",
      "PackageShortCode": "SPR",
      "CoverDate": "2026-08-26T00:00:00.000Z",
      "UpdatedDate": "2026-08-26T22:04:26Z"
    },
    {
      "Name": "SPR_20260825_TopicMerge.xml",
      "ReportName": "Steel Price Report",
      "ReportType": "MARKET REPORT",
      "MimeType": "application/xml",
      "PublicationGrouping": [
        { "Id": "9c5f492b-ba03-4df0-8dac-a6bb2cbdc15b", "MimeType": "application/xml", "Name": "SPR_20260825_TopicMerge.xml" }
      ],
      "Frequency": "Daily",
      "Language": "English",
      "PackageShortCode": "SPR",
      "CoverDate": "2026-08-25T00:00:00.000Z",
      "UpdatedDate": "2026-08-25T21:40:29Z"
    }
  ],
  "TotalPages": 5,
  "TotalRecordCount": 249,
  "Page": 1,
  "PageSize": 50
}
```

O terceiro item não tem PDF no `PublicationGrouping` — é o caso que deve ser descartado.

- [ ] **Step 2: Escrever os testes que falham**

Criar `tests/parseItems.test.js`:

```js
import { describe, expect, it } from 'vitest';

import fixture from './fixtures/blendedsearch-response.json';
import { dateKeyFromCoverDate, paginationOf, parseItems } from '../src/api/parseItems.js';

describe('dateKeyFromCoverDate', () => {
    it('extrai YYYY-MM-DD do ISO', () => {
        expect(dateKeyFromCoverDate('2026-08-27T00:00:00.000Z')).toBe('2026-08-27');
    });

    it('não desloca o dia em fuso negativo', () => {
        const cover = '2026-08-27T00:00:00.000Z';
        // A abordagem ingênua, para contraste: em UTC-3 isto é dia 26.
        const naive = new Date(cover).toLocaleDateString('en-CA', { timeZone: 'America/Sao_Paulo' });
        expect(naive).toBe('2026-08-26');
        // A nossa não passa por Date.
        expect(dateKeyFromCoverDate(cover)).toBe('2026-08-27');
    });

    it('devolve null para entrada inválida', () => {
        expect(dateKeyFromCoverDate('')).toBeNull();
        expect(dateKeyFromCoverDate(null)).toBeNull();
        expect(dateKeyFromCoverDate('27/08/2026')).toBeNull();
    });
});

describe('parseItems', () => {
    it('escolhe o Id do application/pdf, não o do XML', () => {
        const rows = parseItems(fixture);
        expect(rows[0].id).toBe('8174a051-a9a5-43c7-9817-e8257efec05a');
        expect(rows[0].fileName).toBe('SPR_20260827.pdf');
    });

    it('descarta item sem PDF no PublicationGrouping', () => {
        const rows = parseItems(fixture);
        expect(rows).toHaveLength(2);
        expect(rows.map((r) => r.dateKey)).toEqual(['2026-08-27', '2026-08-26']);
    });

    it('normaliza os campos que o upload precisa', () => {
        expect(parseItems(fixture)[0]).toEqual({
            id: '8174a051-a9a5-43c7-9817-e8257efec05a',
            fileName: 'SPR_20260827.pdf',
            reportName: 'Steel Price Report',
            dateKey: '2026-08-27',
            frequency: 'Daily',
            coverDate: '2026-08-27T00:00:00.000Z',
        });
    });

    it('devolve lista vazia quando não há Items', () => {
        expect(parseItems({})).toEqual([]);
        expect(parseItems(null)).toEqual([]);
    });
});

describe('paginationOf', () => {
    it('lê os campos explícitos da resposta', () => {
        expect(paginationOf(fixture)).toEqual({ page: 1, totalPages: 5, totalRecords: 249 });
    });

    it('assume zero páginas quando a resposta não traz os campos', () => {
        expect(paginationOf({})).toEqual({ page: 1, totalPages: 0, totalRecords: 0 });
    });
});
```

- [ ] **Step 3: Rodar e confirmar que falha**

```bash
cd actors/platts-scrap-reports && npx vitest run tests/parseItems.test.js
```

Esperado: FAIL — `Failed to load url ../src/api/parseItems.js`.

- [ ] **Step 4: Implementar**

Criar `src/api/parseItems.js`:

```js
/**
 * Normaliza a resposta do blendedsearch.
 *
 * Cada Item traz um PublicationGrouping com o PDF e um _TopicMerge.xml — a
 * seleção é por MimeType, nunca por índice.
 */

/**
 * CoverDate vem como "2026-08-27T00:00:00.000Z": meia-noite UTC.
 * Passar isso por `new Date()` e formatar em local devolve o dia anterior em
 * qualquer fuso negativo, então a extração é sobre a string.
 */
export function dateKeyFromCoverDate(coverDate) {
    if (typeof coverDate !== 'string') return null;
    const match = coverDate.match(/^(\d{4}-\d{2}-\d{2})/);
    return match ? match[1] : null;
}

export function parseItems(response) {
    const items = Array.isArray(response?.Items) ? response.Items : [];
    return items
        .map((item) => {
            const pdf = (item.PublicationGrouping || []).find((g) => g.MimeType === 'application/pdf');
            const dateKey = dateKeyFromCoverDate(item.CoverDate);
            if (!pdf?.Id || !dateKey || !item.ReportName) return null;
            return {
                id: pdf.Id,
                fileName: pdf.Name,
                reportName: item.ReportName,
                dateKey,
                frequency: item.Frequency || null,
                coverDate: item.CoverDate,
            };
        })
        .filter(Boolean);
}

export function paginationOf(response) {
    return {
        page: response?.Page ?? 1,
        totalPages: response?.TotalPages ?? 0,
        totalRecords: response?.TotalRecordCount ?? 0,
    };
}
```

- [ ] **Step 5: Rodar e confirmar que passa**

```bash
npx vitest run tests/parseItems.test.js
```

Esperado: PASS, 9 testes.

- [ ] **Step 6: Lint e commit**

```bash
npx eslint --fix src/api/parseItems.js tests/parseItems.test.js && npx eslint src/api/parseItems.js tests/parseItems.test.js
git add src/api/parseItems.js tests/parseItems.test.js tests/fixtures/blendedsearch-response.json
git commit -m "feat(backfill): parseItems normaliza a resposta do blendedsearch

Extrai o dateKey de CoverDate por string, sem passar por Date: o valor é
meia-noite UTC e o parse local devolveria o dia anterior em UTC-3.

Seleciona o Id por MimeType application/pdf — cada Item traz também um
_TopicMerge.xml no mesmo PublicationGrouping."
```

---

### Task 2: searchPayload — corpo da busca e janela de datas

**Files:**
- Create: `src/api/searchPayload.js`
- Test: `tests/searchPayload.test.js`

**Interfaces:**
- Consumes: nada.
- Produces: `buildSearchPayload({publication, contentType, fromDate, toDate, page, pageSize}) -> object`; `isoWindow(fromYmd: string, toYmd: string) -> {fromDate: string, toDate: string}`.

- [ ] **Step 1: Escrever os testes que falham**

Criar `tests/searchPayload.test.js`:

```js
import { describe, expect, it } from 'vitest';

import { buildSearchPayload, isoWindow } from '../src/api/searchPayload.js';

describe('isoWindow', () => {
    it('cobre o dia inteiro nas duas pontas', () => {
        expect(isoWindow('2025-01-01', '2025-12-31')).toEqual({
            fromDate: '2025-01-01T00:00:00.000Z',
            toDate: '2025-12-31T23:59:59.999Z',
        });
    });

    it('rejeita formato que não seja YYYY-MM-DD', () => {
        expect(() => isoWindow('01/01/2025', '2025-12-31')).toThrow(/YYYY-MM-DD/);
    });
});

describe('buildSearchPayload', () => {
    const base = { publication: 'Steel Price Report', fromDate: '2025-01-01T00:00:00.000Z', toDate: '2025-12-31T23:59:59.999Z' };

    it('reproduz o corpo que a própria UI do Platts envia', () => {
        expect(buildSearchPayload({ ...base, page: 1 })).toEqual({
            sector: [], geography: [], brand: [], commodity: [],
            contentType: ['Market Reports'],
            linkType: '', mimeType: '',
            subjectType: 'Subject', subject: [],
            fromDate: '2025-01-01T00:00:00.000Z',
            toDate: '2025-12-31T23:59:59.999Z',
            page: 1, pageSize: 50,
            query: '() AND ()',
            isArchived: true,
            publication: ['Steel Price Report'],
            frequency: [], groupBy: '',
            isAllPublicationsSelected: false,
            language: [],
            isChapteredContent: true,
            sort: '',
            spotEnabled: true,
        });
    });

    it('isArchived é sempre true — é o que alcança o histórico', () => {
        expect(buildSearchPayload({ ...base, page: 3 }).isArchived).toBe(true);
    });

    it('propaga a página pedida', () => {
        expect(buildSearchPayload({ ...base, page: 4 }).page).toBe(4);
    });
});
```

- [ ] **Step 2: Rodar e confirmar que falha**

```bash
npx vitest run tests/searchPayload.test.js
```

Esperado: FAIL — módulo não existe.

- [ ] **Step 3: Implementar**

Criar `src/api/searchPayload.js`:

```js
const YMD = /^\d{4}-\d{2}-\d{2}$/;

/**
 * Converte duas datas YYYY-MM-DD numa janela ISO fechada nos dois extremos.
 * CoverDate é meia-noite UTC, então este intervalo captura exatamente as
 * edições cuja capa cai entre as duas datas, inclusive.
 */
export function isoWindow(fromYmd, toYmd) {
    if (!YMD.test(fromYmd) || !YMD.test(toYmd)) {
        throw new Error(`isoWindow espera datas YYYY-MM-DD, recebeu "${fromYmd}" e "${toYmd}"`);
    }
    return { fromDate: `${fromYmd}T00:00:00.000Z`, toDate: `${toYmd}T23:59:59.999Z` };
}

/**
 * Corpo do POST /content-bff/v4/search/blendedsearch.
 *
 * Os campos vazios e as constantes estranhas (`query: '() AND ()'`) são
 * reproduzidos verbatim do que a própria UI envia. Não sabemos quais são
 * obrigatórios, e divergir sem motivo é convidar um 400 silencioso.
 */
export function buildSearchPayload({
    publication,
    contentType = 'Market Reports',
    fromDate,
    toDate,
    page = 1,
    pageSize = 50,
}) {
    return {
        sector: [], geography: [], brand: [], commodity: [],
        contentType: [contentType],
        linkType: '', mimeType: '',
        subjectType: 'Subject', subject: [],
        fromDate, toDate,
        page, pageSize,
        query: '() AND ()',
        isArchived: true,
        publication: [publication],
        frequency: [], groupBy: '',
        isAllPublicationsSelected: false,
        language: [],
        isChapteredContent: true,
        sort: '',
        spotEnabled: true,
    };
}
```

- [ ] **Step 4: Rodar e confirmar que passa**

```bash
npx vitest run tests/searchPayload.test.js
```

Esperado: PASS, 5 testes.

- [ ] **Step 5: Lint e commit**

```bash
npx eslint --fix src/api/searchPayload.js tests/searchPayload.test.js && npx eslint src/api/searchPayload.js tests/searchPayload.test.js
git add src/api/searchPayload.js tests/searchPayload.test.js
git commit -m "feat(backfill): monta o corpo do blendedsearch e a janela ISO

Campos vazios e a constante query '() AND ()' são reproduzidos verbatim do
que a UI do Platts envia — não sabemos quais são obrigatórios."
```

---

### Task 3: plattsApi — executar search e stream com retry

**Files:**
- Create: `src/api/plattsApi.js`
- Test: `tests/plattsApi.test.js`

**Interfaces:**
- Consumes: nada.
- Produces: `createPlattsApi({request, auth, sleep, maxRetries}) -> {searchArchive(payload) -> Promise<object>, fetchPdf(id) -> Promise<Buffer>}`. `request` é `{post(url, {headers, data}) -> Promise<{status, json(), body()}>, get(url, {headers}) -> Promise<{status, json(), body()}>}`. `auth` é `{headers() -> object, refresh() -> Promise<void>}`. Também exporta `playwrightRequest(ctx) -> request`.

- [ ] **Step 1: Escrever os testes que falham**

Criar `tests/plattsApi.test.js`:

```js
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
```

- [ ] **Step 2: Rodar e confirmar que falha**

```bash
npx vitest run tests/plattsApi.test.js
```

Esperado: FAIL — módulo não existe.

- [ ] **Step 3: Implementar**

Criar `src/api/plattsApi.js`:

```js
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
```

- [ ] **Step 4: Rodar e confirmar que passa**

```bash
npx vitest run tests/plattsApi.test.js
```

Esperado: PASS, 9 testes.

- [ ] **Step 5: Lint e commit**

```bash
npx eslint --fix src/api/plattsApi.js tests/plattsApi.test.js && npx eslint src/api/plattsApi.js tests/plattsApi.test.js
git add src/api/plattsApi.js tests/plattsApi.test.js
git commit -m "feat(backfill): cliente do blendedsearch e do stream com retry

401 e 403 disparam recaptura do token e uma retentativa; 429 e 5xx entram em
backoff exponencial; 4xx restante falha na hora, porque é erro nosso."
```

---

### Task 4: captureAuth — obter o Bearer do tráfego da página

**Files:**
- Create: `src/api/captureAuth.js`
- Test: `tests/captureAuth.test.js`

**Interfaces:**
- Consumes: nada.
- Produces: `attachAuthCapture(page) -> {headers: object|null}` (estado mutável, preenchido pelo listener); `waitForAuth(state, {timeoutMs, intervalMs, sleep, now}) -> Promise<object>`.

- [ ] **Step 1: Escrever os testes que falham**

Criar `tests/captureAuth.test.js`:

```js
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
```

- [ ] **Step 2: Rodar e confirmar que falha**

```bash
npx vitest run tests/captureAuth.test.js
```

Esperado: FAIL — módulo não existe.

- [ ] **Step 3: Implementar**

Criar `src/api/captureAuth.js`:

```js
const API_HOST = 'api.platts.com';

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
        if (!request.url().includes(API_HOST)) return;
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
```

- [ ] **Step 4: Rodar e confirmar que passa**

```bash
npx vitest run tests/captureAuth.test.js
```

Esperado: PASS, 8 testes.

- [ ] **Step 5: Lint e commit**

```bash
npx eslint --fix src/api/captureAuth.js tests/captureAuth.test.js && npx eslint src/api/captureAuth.js tests/captureAuth.test.js
git add src/api/captureAuth.js tests/captureAuth.test.js
git commit -m "feat(backfill): captura o Bearer do tráfego da página

O SPA do Platts autentica por JWT, não por cookie, então um fetch nosso não
herda a sessão — o token é copiado de uma requisição real da própria página."
```

---

### Task 5: mapLimit — concorrência limitada

**Files:**
- Create: `src/util/concurrency.js`
- Test: `tests/concurrency.test.js`

**Interfaces:**
- Consumes: nada.
- Produces: `mapLimit(items: Array, limit: number, fn: (item, index) => Promise<T>) -> Promise<Array<T>>`.

- [ ] **Step 1: Escrever os testes que falham**

Criar `tests/concurrency.test.js`:

```js
import { describe, expect, it } from 'vitest';

import { mapLimit } from '../src/util/concurrency.js';

const tick = () => new Promise((resolve) => { setTimeout(resolve, 1); });

describe('mapLimit', () => {
    it('preserva a ordem dos resultados', async () => {
        const out = await mapLimit([1, 2, 3, 4, 5], 2, async (n) => { await tick(); return n * 2; });
        expect(out).toEqual([2, 4, 6, 8, 10]);
    });

    it('nunca ultrapassa o limite de tarefas simultâneas', async () => {
        let running = 0;
        let peak = 0;
        await mapLimit([1, 2, 3, 4, 5, 6, 7], 3, async () => {
            running += 1;
            peak = Math.max(peak, running);
            await tick();
            running -= 1;
        });
        expect(peak).toBe(3);
    });

    it('lida com lista vazia', async () => {
        expect(await mapLimit([], 3, async () => 1)).toEqual([]);
    });

    it('lida com limite maior que a lista', async () => {
        expect(await mapLimit([1, 2], 10, async (n) => n)).toEqual([1, 2]);
    });

    it('propaga a rejeição da função', async () => {
        await expect(mapLimit([1, 2, 3], 2, async (n) => {
            if (n === 2) throw new Error('estourou no 2');
            return n;
        })).rejects.toThrow('estourou no 2');
    });

    it('passa o índice para a função', async () => {
        expect(await mapLimit(['a', 'b'], 1, async (item, i) => `${i}:${item}`)).toEqual(['0:a', '1:b']);
    });
});
```

- [ ] **Step 2: Rodar e confirmar que falha**

```bash
npx vitest run tests/concurrency.test.js
```

Esperado: FAIL — módulo não existe.

- [ ] **Step 3: Implementar**

Criar `src/util/concurrency.js`:

```js
/**
 * Executa `fn` sobre `items` com no máximo `limit` tarefas simultâneas,
 * preservando a ordem dos resultados.
 *
 * O backfill baixa centenas de PDFs; sem limite, dispararíamos todos de uma
 * vez contra o Platts. Com limite baixo, o trabalho leva dezenas de minutos e
 * ninguém se irrita.
 */
export async function mapLimit(items, limit, fn) {
    const results = new Array(items.length);
    let cursor = 0;

    const worker = async () => {
        for (;;) {
            const index = cursor;
            cursor += 1;
            if (index >= items.length) return;
            results[index] = await fn(items[index], index);
        }
    };

    const size = Math.max(1, Math.min(limit, items.length));
    await Promise.all(Array.from({ length: size }, worker));
    return results;
}
```

- [ ] **Step 4: Rodar e confirmar que passa**

```bash
npx vitest run tests/concurrency.test.js
```

Esperado: PASS, 6 testes.

- [ ] **Step 5: Lint e commit**

```bash
npx eslint --fix src/util/concurrency.js tests/concurrency.test.js && npx eslint src/util/concurrency.js tests/concurrency.test.js
git add src/util/concurrency.js tests/concurrency.test.js
git commit -m "feat(backfill): mapLimit para concorrência limitada"
```

---

### Task 6: backfillSummary — o resumo único no Telegram

**Files:**
- Create: `src/notify/backfillSummary.js`
- Test: `tests/backfillSummary.test.js`

**Interfaces:**
- Consumes: nada.
- Produces: `buildBackfillSummaryText(summary) -> string`; `sendBackfillSummary(botToken, chatId, summary) -> Promise<number|null>`. `summary` tem a forma `{downloaded: Array, skipped: Array, errors: Array<{stage, message}>, publications: Array<{publication, totalRecords, processed}>, durationMs: number}`.

- [ ] **Step 1: Escrever os testes que falham**

Criar `tests/backfillSummary.test.js`:

```js
import { describe, expect, it } from 'vitest';

import { buildBackfillSummaryText } from '../src/notify/backfillSummary.js';

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

    it('não usa marcação Markdown — o texto vai com parse_mode desligado', () => {
        const text = buildBackfillSummaryText(summary);
        expect(text).not.toMatch(/[*_`]/);
    });

    it('lida com um backfill sem nenhum erro', () => {
        const text = buildBackfillSummaryText({ ...summary, errors: [] });
        expect(text).toContain('sem erros');
    });
});
```

- [ ] **Step 2: Rodar e confirmar que falha**

```bash
npx vitest run tests/backfillSummary.test.js
```

Esperado: FAIL — módulo não existe.

- [ ] **Step 3: Implementar**

Criar `src/notify/backfillSummary.js`:

```js
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
```

- [ ] **Step 4: Rodar e confirmar que passa**

```bash
npx vitest run tests/backfillSummary.test.js
```

Esperado: PASS, 5 testes.

- [ ] **Step 5: Lint e commit**

```bash
npx eslint --fix src/notify/backfillSummary.js tests/backfillSummary.test.js && npx eslint src/notify/backfillSummary.js tests/backfillSummary.test.js
git add src/notify/backfillSummary.js tests/backfillSummary.test.js
git commit -m "feat(backfill): resumo unico no Telegram, sem botoes e sem Markdown

Novecentos botoes inline estouram o limite do Telegram. O texto vai sem
parse_mode porque carrega nomes e mensagens de erro cruas."
```

---

### Task 7: runBackfill — a orquestração

**Files:**
- Create: `src/backfill/runBackfill.js`
- Test: `tests/runBackfill.test.js`

**Interfaces:**
- Consumes: `parseItems`, `paginationOf` (Task 1); `buildSearchPayload` (Task 2); `mapLimit` (Task 5); `isAlreadyStored(slug, dateKey)`, `uploadPdf(buffer, {storagePath, metadata})` de `src/persist/supabaseUpload.js`; `describeFileFormat(buffer)` de `src/download/capturePdf.js`; `slugify` de `src/util/slug.js`; `datePartsFromIso` de `src/util/dates.js`.
- Produces: `runBackfill(deps) -> Promise<summary>`; `class ZeroYieldError extends Error`.

`deps` = `{api, publications: string[], fromDate, toDate, reportType, concurrency, isAlreadyStored, uploadPdf, now}`.

- [ ] **Step 1: Escrever os testes que falham**

Criar `tests/runBackfill.test.js`:

```js
import { describe, expect, it, vi } from 'vitest';

import { runBackfill, ZeroYieldError } from '../src/backfill/runBackfill.js';

const PDF = Buffer.from('%PDF-1.7 x');
const XLSX = Buffer.concat([Buffer.from('504b0304', 'hex'), Buffer.alloc(16)]);

/** Resposta de uma página do blendedsearch com N itens sintéticos. */
function pageOf(publication, dates, { totalPages = 1, totalRecords = dates.length } = {}) {
    return {
        Items: dates.map((d) => ({
            Name: `${d}.pdf`,
            ReportName: publication,
            CoverDate: `${d}T00:00:00.000Z`,
            Frequency: 'Daily',
            PublicationGrouping: [{ Id: `id-${d}`, MimeType: 'application/pdf', Name: `${d}.pdf` }],
        })),
        TotalPages: totalPages,
        TotalRecordCount: totalRecords,
        Page: 1,
        PageSize: 50,
    };
}

function deps(overrides = {}) {
    return {
        api: {
            searchArchive: vi.fn(async () => pageOf('Steel Price Report', ['2025-01-02', '2025-01-03'])),
            fetchPdf: vi.fn(async () => PDF),
        },
        publications: ['Steel Price Report'],
        fromDate: '2025-01-01T00:00:00.000Z',
        toDate: '2025-12-31T23:59:59.999Z',
        reportType: 'Market Reports',
        concurrency: 2,
        isAlreadyStored: vi.fn(async () => false),
        uploadPdf: vi.fn(async () => ({ id: 'row-1' })),
        now: () => 0,
        ...overrides,
    };
}

describe('runBackfill', () => {
    it('baixa e sobe cada edição encontrada', async () => {
        const d = deps();
        const summary = await runBackfill(d);

        expect(summary.downloaded).toHaveLength(2);
        expect(d.api.fetchPdf).toHaveBeenCalledTimes(2);
        expect(d.uploadPdf).toHaveBeenCalledTimes(2);
    });

    it('monta o storagePath no mesmo formato do fluxo diário', async () => {
        const d = deps();
        await runBackfill(d);

        const [, opts] = d.uploadPdf.mock.calls[0];
        expect(opts.storagePath).toBe('market-reports/2025/01/2025-01-02_steel-price-report.pdf');
        expect(opts.metadata).toMatchObject({
            slug: 'steel-price-report',
            dateKey: '2025-01-02',
            reportName: 'Steel Price Report',
            reportType: 'Market Reports',
        });
    });

    it('pula o que o dedup do Supabase já tem', async () => {
        const d = deps({ isAlreadyStored: vi.fn(async (_slug, dateKey) => dateKey === '2025-01-02') });
        const summary = await runBackfill(d);

        expect(summary.skipped).toHaveLength(1);
        expect(summary.downloaded).toHaveLength(1);
        expect(d.api.fetchPdf).toHaveBeenCalledTimes(1);
    });

    it('registra e segue quando o arquivo não é PDF', async () => {
        const d = deps({ api: {
            searchArchive: vi.fn(async () => pageOf('Steel Price Report', ['2025-01-02', '2025-01-03'])),
            fetchPdf: vi.fn(async (id) => (id === 'id-2025-01-02' ? XLSX : PDF)),
        } });
        const summary = await runBackfill(d);

        expect(summary.downloaded).toHaveLength(1);
        expect(summary.errors).toHaveLength(1);
        expect(summary.errors[0].message).toMatch(/xlsx|zip/i);
        expect(summary.type).toBe('partial');
    });

    it('percorre todas as páginas que TotalPages anuncia', async () => {
        const searchArchive = vi.fn()
            .mockResolvedValueOnce(pageOf('Steel Price Report', ['2025-01-02'], { totalPages: 3, totalRecords: 3 }))
            .mockResolvedValueOnce(pageOf('Steel Price Report', ['2025-01-03'], { totalPages: 3, totalRecords: 3 }))
            .mockResolvedValueOnce(pageOf('Steel Price Report', ['2025-01-06'], { totalPages: 3, totalRecords: 3 }));
        const d = deps({ api: { searchArchive, fetchPdf: vi.fn(async () => PDF) } });

        const summary = await runBackfill(d);

        expect(searchArchive).toHaveBeenCalledTimes(3);
        expect(summary.downloaded).toHaveLength(3);
        expect(searchArchive.mock.calls[2][0].page).toBe(3);
    });

    it('uma página curta no meio não encerra o laço', async () => {
        const searchArchive = vi.fn()
            .mockResolvedValueOnce(pageOf('Steel Price Report', [], { totalPages: 2, totalRecords: 1 }))
            .mockResolvedValueOnce(pageOf('Steel Price Report', ['2025-01-03'], { totalPages: 2, totalRecords: 1 }));
        const d = deps({ api: { searchArchive, fetchPdf: vi.fn(async () => PDF) } });

        await runBackfill(d);
        expect(searchArchive).toHaveBeenCalledTimes(2);
    });

    it('lança ZeroYieldError quando a API anuncia registros e nada é extraído', async () => {
        const d = deps({ api: {
            searchArchive: vi.fn(async () => ({ Items: [], TotalPages: 1, TotalRecordCount: 249, Page: 1 })),
            fetchPdf: vi.fn(),
        } });

        await expect(runBackfill(d)).rejects.toThrow(ZeroYieldError);
    });

    it('não confunde publicação genuinamente vazia com rendimento zero', async () => {
        const d = deps({ api: {
            searchArchive: vi.fn(async () => ({ Items: [], TotalPages: 0, TotalRecordCount: 0, Page: 1 })),
            fetchPdf: vi.fn(),
        } });

        const summary = await runBackfill(d);
        expect(summary.type).toBe('success');
        expect(summary.downloaded).toHaveLength(0);
    });

    it('segue para a próxima publicação quando uma falha ao listar', async () => {
        const searchArchive = vi.fn()
            .mockRejectedValueOnce(new Error('rede caiu'))
            .mockResolvedValueOnce(pageOf('Cement Weekly', ['2025-01-03']));
        const d = deps({
            publications: ['Steel Price Report', 'Cement Weekly'],
            api: { searchArchive, fetchPdf: vi.fn(async () => PDF) },
        });

        const summary = await runBackfill(d);

        expect(summary.errors[0].stage).toBe('publication');
        expect(summary.downloaded).toHaveLength(1);
        expect(summary.type).toBe('partial');
    });

    it('não engole o ZeroYieldError no catch por publicação', async () => {
        const d = deps({
            publications: ['A', 'B'],
            api: {
                searchArchive: vi.fn(async () => ({ Items: [], TotalPages: 1, TotalRecordCount: 10, Page: 1 })),
                fetchPdf: vi.fn(),
            },
        });

        await expect(runBackfill(d)).rejects.toThrow(ZeroYieldError);
    });
});
```

- [ ] **Step 2: Rodar e confirmar que falha**

```bash
npx vitest run tests/runBackfill.test.js
```

Esperado: FAIL — módulo não existe.

- [ ] **Step 3: Implementar**

Criar `src/backfill/runBackfill.js`:

```js
import { log } from 'crawlee';

import { paginationOf, parseItems } from '../api/parseItems.js';
import { buildSearchPayload } from '../api/searchPayload.js';
import { describeFileFormat } from '../download/capturePdf.js';
import { mapLimit } from '../util/concurrency.js';
import { datePartsFromIso } from '../util/dates.js';
import { slugify } from '../util/slug.js';

/**
 * A API respondeu, anunciou registros, e nada foi extraído.
 *
 * Isso significa que o contrato da resposta mudou — o mesmo modo de falha que
 * manteve o fluxo diário verde entregando zero por quatro semanas. É erro de
 * run inteiro, não de publicação, então o laço não captura este tipo.
 */
export class ZeroYieldError extends Error {
    constructor(publication, totalRecords) {
        super(
            `Zero yield for "${publication}": the API reported ${totalRecords} records `
            + 'but no item could be parsed. The blendedsearch response shape probably changed.',
        );
        this.name = 'ZeroYieldError';
    }
}

async function handleItem(row, deps, summary) {
    const slug = slugify(row.reportName);
    const parts = datePartsFromIso(row.dateKey);
    if (!slug || !parts) {
        summary.errors.push({ stage: 'parse-row', reportName: row.reportName, message: 'missing slug or dateKey' });
        summary.type = 'partial';
        return;
    }

    if (await deps.isAlreadyStored(slug, row.dateKey)) {
        summary.skipped.push({ slug, dateKey: row.dateKey, reason: 'already-exists' });
        return;
    }

    let buffer;
    try {
        buffer = await deps.api.fetchPdf(row.id);
    } catch (e) {
        summary.errors.push({ stage: 'download', reportName: row.reportName, message: e.message });
        summary.type = 'partial';
        return;
    }

    const format = describeFileFormat(buffer);
    if (format !== 'PDF') {
        summary.errors.push({ stage: 'download', reportName: row.reportName, message: `Downloaded file is ${format}` });
        summary.type = 'partial';
        return;
    }

    const storagePath = `${slugify(deps.reportType)}/${parts.year}/${parts.month}/${row.dateKey}_${slug}.pdf`;

    try {
        await deps.uploadPdf(buffer, {
            storagePath,
            metadata: {
                slug,
                dateKey: row.dateKey,
                reportName: row.reportName,
                reportType: deps.reportType,
                frequency: row.frequency,
                coverDate: row.coverDate,
                publishedDate: null,
            },
        });
    } catch (e) {
        summary.errors.push({ stage: 'supabase-upload', reportName: row.reportName, message: e.message });
        summary.type = 'partial';
        return;
    }

    summary.downloaded.push({ slug, dateKey: row.dateKey, storagePath });
}

async function backfillPublication(publication, deps, summary) {
    let page = 1;
    let totalPages = 1;
    let totalRecords = 0;
    let processed = 0;

    while (page <= totalPages) {
        const payload = buildSearchPayload({
            publication,
            contentType: deps.reportType,
            fromDate: deps.fromDate,
            toDate: deps.toDate,
            page,
        });
        const response = await deps.api.searchArchive(payload);
        const pagination = paginationOf(response);
        totalPages = pagination.totalPages;
        totalRecords = pagination.totalRecords;

        const rows = parseItems(response);
        processed += rows.length;
        log.info(`${publication} page ${page}/${totalPages}: ${rows.length} item(s)`);

        await mapLimit(rows, deps.concurrency, (row) => handleItem(row, deps, summary));
        page += 1;
    }

    if (totalRecords > 0 && processed === 0) {
        throw new ZeroYieldError(publication, totalRecords);
    }

    return { publication, totalRecords, processed };
}

export async function runBackfill(deps) {
    const startedAt = deps.now();
    const summary = {
        type: 'success',
        downloaded: [],
        skipped: [],
        errors: [],
        publications: [],
        durationMs: 0,
    };

    for (const publication of deps.publications) {
        try {
            summary.publications.push(await backfillPublication(publication, deps, summary));
        } catch (e) {
            // O guard de rendimento zero derruba o run inteiro de propósito.
            if (e instanceof ZeroYieldError) throw e;
            summary.errors.push({ stage: 'publication', publication, message: e.message });
            summary.type = 'partial';
        }
    }

    summary.durationMs = deps.now() - startedAt;
    return summary;
}
```

- [ ] **Step 4: Rodar e confirmar que passa**

```bash
npx vitest run tests/runBackfill.test.js
```

Esperado: PASS, 10 testes.

- [ ] **Step 5: Rodar a suíte inteira — nada do fluxo diário pode quebrar**

```bash
npm test
```

Esperado: PASS. Contagem anterior era 43; com as tasks 1 a 7 deve chegar a 95.

- [ ] **Step 6: Lint e commit**

```bash
npx eslint --fix src/backfill/runBackfill.js tests/runBackfill.test.js && npx eslint src/backfill/runBackfill.js tests/runBackfill.test.js
git add src/backfill/runBackfill.js tests/runBackfill.test.js
git commit -m "feat(backfill): orquestra publicacoes, paginas, dedup e upload

Pagina por TotalPages, nao por heuristica de pagina curta. Falha de uma
publicacao nao derruba as outras, mas ZeroYieldError derruba: a API responder
e nada ser extraido e o modo de falha que manteve o fluxo diario verde
entregando zero por quatro semanas."
```

---

### Task 8: Wiring no main.js e no input schema

**Files:**
- Modify: `src/main.js`
- Modify: `.actor/input_schema.json`
- Test: `tests/backfillWiring.test.js`

**Interfaces:**
- Consumes: tudo das tasks 1 a 7.
- Produces: `resolveBackfillPublications(rows, excludes) -> string[]` exportado de `src/backfill/runBackfill.js`.

- [ ] **Step 1: Escrever o teste da seleção de publicações**

Criar `tests/backfillWiring.test.js`:

```js
import { describe, expect, it } from 'vitest';

import { DEFAULT_EXCLUDES } from '../src/filters/applyFilters.js';
import { resolveBackfillPublications } from '../src/backfill/runBackfill.js';

const gridRows = [
    { reportName: 'Steel Price Report' },
    { reportName: 'Steel Business Briefing' },
    { reportName: 'Cement Weekly' },
    { reportName: 'Panorama Semanal' },
    { reportName: 'Global Market Outlook (Português)' },
    { reportName: 'Global Market Outlook' },
];

describe('resolveBackfillPublications', () => {
    it('usa os nomes do grid menos os excludes padrão', () => {
        expect(resolveBackfillPublications(gridRows, DEFAULT_EXCLUDES)).toEqual([
            'Steel Price Report',
            'Steel Business Briefing',
            'Cement Weekly',
            'Global Market Outlook',
        ]);
    });

    it('remove nomes repetidos', () => {
        const dup = [...gridRows, { reportName: 'Cement Weekly' }];
        const out = resolveBackfillPublications(dup, DEFAULT_EXCLUDES);
        expect(out.filter((n) => n === 'Cement Weekly')).toHaveLength(1);
    });

    it('devolve vazio quando o grid não trouxe nada', () => {
        expect(resolveBackfillPublications([], DEFAULT_EXCLUDES)).toEqual([]);
    });
});
```

- [ ] **Step 2: Rodar e confirmar que falha**

```bash
npx vitest run tests/backfillWiring.test.js
```

Esperado: FAIL — `resolveBackfillPublications` não é exportado.

- [ ] **Step 3: Implementar a seleção**

Acrescentar ao topo dos imports de `src/backfill/runBackfill.js`:

```js
import { applyExcludeFilter } from '../filters/applyFilters.js';
```

E exportar, logo depois da classe `ZeroYieldError`:

```js
/**
 * O grid diz o que existe; a API diz o histórico de cada um. Derivar a lista
 * daqui em vez de fixar nomes no código mantém o backfill em dia se o Platts
 * adicionar um relatório.
 */
export function resolveBackfillPublications(gridRows, excludes) {
    const kept = applyExcludeFilter(gridRows, excludes).map((r) => r.reportName);
    return [...new Set(kept)];
}
```

- [ ] **Step 4: Rodar e confirmar que passa**

```bash
npx vitest run tests/backfillWiring.test.js
```

Esperado: PASS, 3 testes.

- [ ] **Step 5: Acrescentar os campos ao input schema**

Em `.actor/input_schema.json`, dentro de `properties`, acrescentar:

```json
"mode": {
  "title": "Mode",
  "type": "string",
  "description": "daily (padrao) le o grid do dia. backfill baixa um intervalo historico via API.",
  "editor": "select",
  "enum": ["daily", "backfill"],
  "default": "daily"
},
"backfillFrom": {
  "title": "Backfill from (YYYY-MM-DD)",
  "type": "string",
  "description": "Primeira data de capa a incluir. So usado quando mode=backfill.",
  "editor": "textfield"
},
"backfillTo": {
  "title": "Backfill to (YYYY-MM-DD)",
  "type": "string",
  "description": "Ultima data de capa a incluir. So usado quando mode=backfill.",
  "editor": "textfield"
},
"backfillPublications": {
  "title": "Backfill publications",
  "type": "array",
  "description": "Vazio = todas as do grid menos os excludes.",
  "editor": "stringList",
  "default": []
},
"backfillConcurrency": {
  "title": "Backfill concurrency",
  "type": "integer",
  "description": "Downloads simultaneos.",
  "default": 3,
  "minimum": 1,
  "maximum": 8
}
```

- [ ] **Step 6: Ligar o branch no main.js**

Em `src/main.js`, acrescentar aos imports:

```js
import { attachAuthCapture, waitForAuth } from './api/captureAuth.js';
import { createPlattsApi, playwrightRequest } from './api/plattsApi.js';
import { isoWindow } from './api/searchPayload.js';
import { resolveBackfillPublications, runBackfill } from './backfill/runBackfill.js';
import { sendBackfillSummary } from './notify/backfillSummary.js';
```

Acrescentar à desestruturação do input (depois de `telegramChatId`):

```js
    mode = 'daily',
    backfillFrom,
    backfillTo,
    backfillPublications = [],
    backfillConcurrency = 3,
```

Dentro de `async function run()`, logo depois do bloco que trata `loginResult.ok === false`, inserir:

```js
    if (mode === 'backfill') {
        if (!backfillFrom || !backfillTo) {
            throw new Error('backfillFrom and backfillTo are required when mode=backfill');
        }

        const authState = attachAuthCapture(page);
        const reportType = 'Market Reports';
        await navigateGrid(page, reportType);
        await waitForAuth(authState);

        const publications = backfillPublications.length > 0
            ? backfillPublications
            : resolveBackfillPublications(await extractRows(page), excludeReportNames ?? DEFAULT_EXCLUDES);

        log.info(`Backfill ${backfillFrom}..${backfillTo} over ${publications.length} publication(s)`);

        const { fromDate, toDate } = isoWindow(backfillFrom, backfillTo);
        const api = createPlattsApi({
            request: playwrightRequest(ctx),
            auth: {
                headers: () => authState.headers,
                refresh: async () => {
                    authState.headers = null;
                    await navigateGrid(page, reportType);
                    await waitForAuth(authState);
                },
            },
        });

        const backfillSummary = await runBackfill({
            api,
            publications,
            fromDate,
            toDate,
            reportType,
            concurrency: backfillConcurrency,
            isAlreadyStored,
            uploadPdf,
            now: () => Date.now(),
        });

        Object.assign(summary, backfillSummary);
        if (!dryRun) await sendBackfillSummary(TG_TOKEN, TG_CHAT, backfillSummary);
        await Actor.pushData(summary);
        return;
    }
```

Também acrescentar `DEFAULT_EXCLUDES` ao import existente de `applyFilters.js`:

```js
import { applyExcludeFilter, DEFAULT_EXCLUDES } from './filters/applyFilters.js';
```

- [ ] **Step 7: Rodar a suíte inteira**

```bash
npm test
```

Esperado: PASS, 98 testes. O caminho diário não pode ter nenhuma falha nova.

- [ ] **Step 8: Lint e commit**

```bash
npx eslint --fix src/main.js src/backfill/runBackfill.js tests/backfillWiring.test.js
npx eslint src/main.js src/backfill/runBackfill.js tests/backfillWiring.test.js
git add src/main.js .actor/input_schema.json src/backfill/runBackfill.js tests/backfillWiring.test.js
git commit -m "feat(backfill): liga o modo backfill no main e no input schema

O grid da lista de publicacoes; a API da o historico de cada uma. mode=daily
segue sendo o padrao e nao muda de comportamento."
```

---

### Task 9: Validação em produção

**Files:** nenhum. Esta task é execução e observação.

- [ ] **Step 1: Publicar o build**

```bash
cd actors/platts-scrap-reports && apify push --force -w 420
```

Esperado: `Actor was deployed to Apify cloud and built there.`

- [ ] **Step 2: Ensaio com uma publicação e um mês**

No console do Apify, rodar com:

```json
{
  "mode": "backfill",
  "backfillFrom": "2025-01-01",
  "backfillTo": "2025-01-31",
  "backfillPublications": ["Steel Price Report"],
  "backfillConcurrency": 2,
  "dryRun": false
}
```

Esperado: cerca de 21 PDFs (dias úteis de janeiro), zero erros.

- [ ] **Step 3: Conferir no Supabase que as datas não deslocaram**

```bash
SUPABASE_URL=$(grep '^SUPABASE_URL=' ../../.env | cut -d= -f2-)
SUPABASE_KEY=$(grep '^SUPABASE_KEY=' ../../.env | cut -d= -f2-)
curl -sS "$SUPABASE_URL/rest/v1/platts_reports?select=date_key,report_name&date_key=gte.2025-01-01&date_key=lte.2025-01-31&order=date_key" \
  -H "apikey: $SUPABASE_KEY" -H "Authorization: Bearer $SUPABASE_KEY"
```

Esperado: `date_key` de segunda a sexta de janeiro/2025. **Nenhum sábado ou domingo** — um fim de semana na lista é o sintoma de deslocamento de fuso.

- [ ] **Step 4: Confirmar que rodar de novo não duplica**

Repetir o mesmo run do Step 2.

Esperado: `downloaded: 0`, `skipped: ~21`. É a prova de que o dedup funciona como checkpoint.

- [ ] **Step 5: Rodar o ano inteiro**

```json
{
  "mode": "backfill",
  "backfillFrom": "2025-01-01",
  "backfillTo": "2025-12-31",
  "backfillPublications": [],
  "backfillConcurrency": 3
}
```

Esperado: ~927 baixados, resumo único no Telegram, dezenas de minutos.

- [ ] **Step 6: Confirmar que o fluxo diário continua intacto**

```bash
gh workflow run platts_reports.yml -f dry_run=true
```

Esperado: `Extracted 12 rows (12 named)` e `Extracted 21 rows (21 named)`. O modo diário não pode ter mudado.

---

## Self-Review

**Cobertura da spec:**

| Requisito da spec | Task |
|---|---|
| Chamar blendedsearch com fromDate/toDate/page | 2, 3 |
| Baixar via stream/{Id} | 3 |
| CoverDate ISO sem passar por Date | 1 |
| Selecionar PDF por MimeType, não índice | 1 |
| Paginar por TotalPages | 7 |
| Descobrir publicações pelo grid + excludes | 8 |
| Dedup como checkpoint | 7 |
| 401 → recaptura → retenta | 3, 4 |
| 429/5xx → backoff, 3 tentativas | 3 |
| não-PDF → registra e segue | 7 |
| falha de publicação → segue | 7 |
| rendimento zero → lança | 7 |
| Concorrência 3 | 5, 7, 8 |
| Resumo único no Telegram, sem botões | 6 |
| Input `mode`/`backfillFrom`/`backfillTo`/... | 8 |
| Disparo pelo console do Apify | 9 |
| Mesmo bucket, tabela e storagePath | 7 |

Sem lacunas.

**Consistência de tipos:** `summary` tem a mesma forma em runBackfill (Task 7) e backfillSummary (Task 6): `{type, downloaded[], skipped[], errors[{stage, message}], publications[{publication, totalRecords, processed}], durationMs}`. `deps.api` expõe `searchArchive`/`fetchPdf` — os mesmos nomes que a Task 3 produz. `auth` expõe `headers()`/`refresh()` nas tasks 3 e 8. `uploadPdf(buffer, {storagePath, metadata})` bate com a assinatura real de `src/persist/supabaseUpload.js:28`.

**Riscos conhecidos, a verificar na Task 9:**

- A janela de um ano pode ser recusada pela API; a UI só foi observada pedindo exatamente 365 dias. Se o Step 5 falhar ou vier truncado, quebrar em trimestres — `TotalRecordCount` denuncia truncamento.
- `ctx.request` do Playwright não passa pelo service worker do SPA. Se o Platts exigir algum header que só a página injeta, o Step 2 da Task 9 devolve 400 ou 403 e o conjunto de headers em `captureAuth.js` precisa crescer.
