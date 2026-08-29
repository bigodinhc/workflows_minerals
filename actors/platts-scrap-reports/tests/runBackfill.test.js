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
            UpdatedDate: `${d}T21:54:13Z`,
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
            publishedDate: '2025-01-02T21:54:13Z',
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


    it('respeita o limite de concorrencia ao baixar', async () => {
        let inFlight = 0;
        let peak = 0;
        const dates = ['2025-01-02', '2025-01-03', '2025-01-06', '2025-01-07', '2025-01-08'];
        const d = deps({
            concurrency: 2,
            api: {
                searchArchive: vi.fn(async () => pageOf('Steel Price Report', dates)),
                fetchPdf: vi.fn(async () => {
                    inFlight += 1;
                    peak = Math.max(peak, inFlight);
                    await new Promise((resolve) => { setTimeout(resolve, 5); });
                    inFlight -= 1;
                    return PDF;
                }),
            },
        });

        await runBackfill(d);
        expect(peak).toBe(2);
    });

    it('dispara o guard mesmo quando a ultima pagina perde a contagem', async () => {
        // A pagina 1 anuncia 249 registros; a pagina 2 vem com o envelope
        // malformado. Sem o Math.max, totalRecords zeraria e o guard ficaria cego.
        const searchArchive = vi.fn()
            .mockResolvedValueOnce({ Items: [], TotalPages: 2, TotalRecordCount: 249, Page: 1 })
            .mockResolvedValueOnce({ Items: [], Page: 2 });
        const d = deps({ api: { searchArchive, fetchPdf: vi.fn() } });

        await expect(runBackfill(d)).rejects.toThrow(ZeroYieldError);
    });

    it('lanca quando o run inteiro nao toca em nada', async () => {
        const d = deps({
            publications: ['A', 'B'],
            api: {
                searchArchive: vi.fn(async () => { throw new Error('token expirado'); }),
                fetchPdf: vi.fn(),
            },
        });

        await expect(runBackfill(d)).rejects.toThrow(/whole run/i);
    });

    it('nao lanca quando tudo foi pulado por dedup — re-run legitimo', async () => {
        const d = deps({ isAlreadyStored: vi.fn(async () => true) });

        const summary = await runBackfill(d);
        expect(summary.skipped).toHaveLength(2);
        expect(summary.downloaded).toHaveLength(0);
    });

    it('registra falha de dedup como stage dedup, sem abandonar o resto', async () => {
        const isAlreadyStored = vi.fn()
            .mockRejectedValueOnce(new Error('supabase fora do ar'))
            .mockResolvedValueOnce(false);
        const d = deps({ concurrency: 1, isAlreadyStored });

        const summary = await runBackfill(d);
        expect(summary.errors[0].stage).toBe('dedup');
        expect(summary.downloaded).toHaveLength(1);
        expect(summary.type).toBe('partial');
    });


    it('lanca quando o envelope da primeira pagina nao e reconhecido', async () => {
        const d = deps({ api: {
            searchArchive: vi.fn(async () => ({ data: [], meta: { total: 249 } })),
            fetchPdf: vi.fn(),
        } });

        await expect(runBackfill(d)).rejects.toThrow(/unrecognized/i);
    });

    it('lanca quando TotalRecordCount vem nao-numerico', async () => {
        const d = deps({ api: {
            searchArchive: vi.fn(async () => ({ Items: [], TotalPages: 1, TotalRecordCount: '249 records', Page: 1 })),
            fetchPdf: vi.fn(),
        } });

        await expect(runBackfill(d)).rejects.toThrow(/unrecognized/i);
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

    it('em dryRun baixa e valida, mas nao sobe — registra em would_download', async () => {
        const d = deps({ dryRun: true });

        const summary = await runBackfill(d);

        expect(d.api.fetchPdf).toHaveBeenCalledTimes(2);   // baixou de verdade
        expect(d.uploadPdf).not.toHaveBeenCalled();        // nao subiu
        expect(summary.would_download).toHaveLength(2);
        expect(summary.downloaded).toHaveLength(0);
        expect(summary.would_download[0]).toMatchObject({
            slug: 'steel-price-report',
            dateKey: '2025-01-02',
            storagePath: 'market-reports/2025/01/2025-01-02_steel-price-report.pdf',
        });
    });

    it('em dryRun continua consultando o dedup', async () => {
        const d = deps({ dryRun: true, isAlreadyStored: vi.fn(async () => true) });

        const summary = await runBackfill(d);

        expect(d.isAlreadyStored).toHaveBeenCalledTimes(2);
        expect(summary.skipped).toHaveLength(2);
        expect(d.api.fetchPdf).not.toHaveBeenCalled();
    });

    it('registra erro de paginacao quando TotalPages fica aquem do esperado por TotalRecordCount', async () => {
        // 249 registros anunciados pedem 5 paginas (pageSize 50); TotalPages
        // diz 1. O laco sai apos a pagina 1 com processed=2 (>0), entao nenhum
        // guard de zero-yield disparava antes desta correcao — um run
        // "success" que coletou uma fracao dos dados.
        const d = deps({ api: {
            searchArchive: vi.fn(async () => pageOf('Steel Price Report', ['2025-01-02', '2025-01-03'], {
                totalPages: 1, totalRecords: 249,
            })),
            fetchPdf: vi.fn(async () => PDF),
        } });

        const summary = await runBackfill(d);

        expect(summary.errors).toContainEqual(expect.objectContaining({
            stage: 'pagination',
            publication: 'Steel Price Report',
        }));
        expect(summary.type).toBe('partial');
    });

    it('nao registra erro de paginacao quando TotalPages cobre o esperado', async () => {
        const d = deps();
        const summary = await runBackfill(d);

        expect(summary.errors.filter((e) => e.stage === 'pagination')).toHaveLength(0);
    });

    it('lanca quando TODAS as publicacoes anunciam zero — janela ou nome provavelmente errados', async () => {
        const d = deps({
            publications: ['Steel Price Report', 'Cement Weekly'],
            api: {
                searchArchive: vi.fn(async () => ({ Items: [], TotalPages: 0, TotalRecordCount: 0, Page: 1 })),
                fetchPdf: vi.fn(),
            },
        });

        const error = await runBackfill(d).catch((e) => e);
        expect(error).toBeInstanceOf(ZeroYieldError);
        expect(error.summary).toBeDefined();
        expect(error.summary.publications).toHaveLength(2);
    });

    it('uma UNICA publicacao vazia entre varias nao dispara o guard de todas-zero', async () => {
        const searchArchive = vi.fn()
            .mockResolvedValueOnce({ Items: [], TotalPages: 0, TotalRecordCount: 0, Page: 1 })
            .mockResolvedValueOnce(pageOf('Cement Weekly', ['2025-01-03']));
        const d = deps({
            publications: ['Steel Price Report', 'Cement Weekly'],
            api: { searchArchive, fetchPdf: vi.fn(async () => PDF) },
        });

        const summary = await runBackfill(d);
        expect(summary.type).toBe('success');
        expect(summary.downloaded).toHaveLength(1);
    });

    it('em dryRun um nao-PDF ainda e detectado', async () => {
        const d = deps({ dryRun: true, api: {
            searchArchive: vi.fn(async () => pageOf('Steel Price Report', ['2025-01-02', '2025-01-03'])),
            fetchPdf: vi.fn(async (id) => (id === 'id-2025-01-02' ? XLSX : PDF)),
        } });

        const summary = await runBackfill(d);

        expect(summary.errors).toHaveLength(1);
        expect(summary.would_download).toHaveLength(1);
    });
});
