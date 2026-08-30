import { log } from 'crawlee';

import { paginationOf, parseItems } from '../api/parseItems.js';
import { buildSearchPayload, PAGE_SIZE } from '../api/searchPayload.js';
import { describeFileFormat } from '../download/capturePdf.js';
import { applyExcludeFilter } from '../filters/applyFilters.js';
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
    constructor(message) {
        super(message);
        this.name = 'ZeroYieldError';
    }
}

/**
 * O grid diz o que existe; a API diz o histórico de cada um. Derivar a lista
 * daqui em vez de fixar nomes no código mantém o backfill em dia se o Platts
 * adicionar um relatório.
 */
export function resolveBackfillPublications(gridRows, excludes) {
    const kept = applyExcludeFilter(gridRows, excludes).map((r) => r.reportName);
    return [...new Set(kept)];
}

/**
 * Registra uma falha no sumário e no log do run.
 *
 * O run de 2024 guardou sua única falha de upload no sumário e em nenhum outro
 * lugar: o log do Apify tinha zero ocorrências de "failed". Uma falha que só
 * existe num registro de dataset é uma falha que ninguém vai olhar.
 */
function recordError(acc, entry) {
    acc.errors.push(entry);
    acc.type = 'partial';
    const subject = entry.reportName || entry.publication || '(unknown)';
    log.warning(`[${entry.stage}] ${subject}: ${entry.message}`);
}

async function handleItem(row, deps, acc) {
    const slug = slugify(row.reportName);
    const parts = datePartsFromIso(row.dateKey);
    if (!slug || !parts) {
        recordError(acc, { stage: 'parse-row', reportName: row.reportName, message: 'missing slug or dateKey' });
        return;
    }

    let alreadyStored;
    try {
        alreadyStored = await deps.isAlreadyStored(slug, row.dateKey);
    } catch (e) {
        recordError(acc, { stage: 'dedup', reportName: row.reportName, message: e.message });
        return;
    }
    if (alreadyStored) {
        acc.skipped.push({ slug, dateKey: row.dateKey, reason: 'already-exists' });
        return;
    }

    let buffer;
    try {
        buffer = await deps.api.fetchPdf(row.id);
    } catch (e) {
        recordError(acc, { stage: 'download', reportName: row.reportName, message: e.message });
        return;
    }

    const format = describeFileFormat(buffer);
    if (format !== 'PDF') {
        recordError(acc, { stage: 'download', reportName: row.reportName, message: `Downloaded file is ${format}` });
        return;
    }

    const storagePath = `${slugify(deps.reportType)}/${parts.year}/${parts.month}/${row.dateKey}_${slug}.pdf`;

    if (deps.dryRun) {
        acc.would_download.push({ slug, dateKey: row.dateKey, storagePath, sizeBytes: buffer.length });
        return;
    }

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
                publishedDate: row.updatedDate,
            },
        });
    } catch (e) {
        recordError(acc, { stage: 'supabase-upload', reportName: row.reportName, message: e.message });
        return;
    }

    acc.downloaded.push({ slug, dateKey: row.dateKey, storagePath });
}

async function backfillPublication(publication, deps, acc) {
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
        // paginationOf normaliza campo ausente para 0, o que apaga a diferenca entre
        // "a API disse zero" e "a API mudou de formato". Na primeira pagina exigimos
        // reconhecer o envelope: sem isso, um rename completo (Items + TotalPages +
        // TotalRecordCount) deixa os dois guards mudos e o run fecha verde com zero.
        if (page === 1) {
            const announced = Number(response?.TotalRecordCount);
            const recognized = Array.isArray(response?.Items) && Number.isFinite(announced);
            if (!recognized) {
                throw new ZeroYieldError(
                    `Unrecognized blendedsearch envelope for "${publication}": expected an Items array and a numeric `
                    + 'TotalRecordCount. The response shape changed.',
                );
            }
        }
        totalPages = pagination.totalPages;
        // Math.max, nao atribuicao: se uma pagina vier com o envelope malformado,
        // totalRecords zeraria e o guard abaixo ficaria cego justamente no caso
        // que ele existe para pegar.
        totalRecords = Math.max(totalRecords, pagination.totalRecords);

        const rows = parseItems(response);
        processed += rows.length;
        log.info(`${publication} page ${page}/${totalPages}: ${rows.length} item(s)`);

        await mapLimit(rows, deps.concurrency, (row) => handleItem(row, deps, acc));
        page += 1;
    }

    if (processed < totalRecords) {
        log.warning(
            `${publication}: parsed ${processed} of ${totalRecords} announced records — `
            + 'pages may have been truncated, or items lacked a PDF in PublicationGrouping',
        );
    }

    // Um warning sozinho nao barra nada: se TotalPages vier ausente ou errado
    // (ex.: 249 registros anunciados, TotalPages 1), o laco sai apos a
    // pagina 1, `processed` fica em 50, e como processed > 0 nenhum guard
    // dispara — um run "success" que coletou 20% dos dados. PAGE_SIZE vem de
    // buildSearchPayload (ver searchPayload.js); expectedPages deriva disso.
    const expectedPages = Math.ceil(totalRecords / PAGE_SIZE);
    if (totalRecords > 0 && totalPages < expectedPages) {
        recordError(acc, {
            stage: 'pagination',
            publication,
            message: `Grid announced ${totalRecords} records (${expectedPages} pages) but only ${totalPages} page(s) were offered`,
        });
    }

    if (totalRecords > 0 && processed === 0) {
        throw new ZeroYieldError(
            `Zero yield for "${publication}": the API reported ${totalRecords} records `
            + 'but no item could be parsed. The blendedsearch response shape probably changed.',
        );
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
        would_download: [],
        durationMs: 0,
    };

    for (const publication of deps.publications) {
        try {
            summary.publications.push(await backfillPublication(publication, deps, summary));
        } catch (e) {
            // O guard de rendimento zero derruba o run inteiro de propósito.
            // O check por `name` cobre o caso da classe ser carregada em dois
            // realms de módulo (mock de teste, resolução dupla de pacote),
            // onde `instanceof` falharia em silêncio.
            if (e instanceof ZeroYieldError || e?.name === 'ZeroYieldError') throw e;
            recordError(summary, { stage: 'publication', publication, message: e.message });
        }
    }

    // Guard de rendimento zero no nivel do run: o guard `touched` abaixo so
    // dispara quando `errors.length > 0`. Uma janela de data com typo, um
    // nome de publicacao que a API nao reconhece, ou uma mudanca no que
    // contentType/isArchived seleciona produzem listagens limpas — zero
    // registros, zero erros — e um run verde que baixou zero PDFs. UMA
    // publicacao legitimamente vazia e normal (ex.: relatorio novo, sem
    // historico no periodo) quando a lista vem do grid; TODAS as publicacoes
    // anunciando zero nao e. Por isso o guard exige mais de uma publicacao
    // antes de desconfiar de uma lista derivada do grid — a menos que a
    // lista tenha vindo digitada pelo operador, caso em que ate uma unica
    // publicacao zerada ja e sinal de erro (ver `publicationsFromInput`).
    const listed = summary.publications;
    const allZero = listed.length > 0 && listed.every((p) => p.totalRecords === 0);
    // Lista vinda do grid pode conter publicacao sem historico na janela — isso e legitimo.
    // Lista digitada pelo operador nao: ele nomeou a publicacao E a janela, entao zero e erro dele.
    if (allZero && (listed.length > 1 || deps.publicationsFromInput)) {
        summary.type = 'error';
        summary.durationMs = deps.now() - startedAt;
        const error = new ZeroYieldError(
            'Every publication announced zero records — check the date window and the publication names.',
        );
        error.summary = summary;
        throw error;
    }

    // Guard de run inteiro: mesmo que cada publicação individualmente não
    // dispare o ZeroYieldError (por exemplo, todas falharam ao listar), um
    // run que não baixou nem pulou nada e ainda registrou falhas é
    // indistinguível de "nada funcionou". `skipped` conta como "tocado" de
    // propósito: um re-run sobre um período já completo legitimamente baixa
    // zero, e isso não pode disparar o guard. O gate em `errors.length > 0`
    // existe pela mesma razão: uma publicação genuinamente vazia (a API
    // anuncia TotalRecordCount 0 e não erra em nada) também toca zero itens,
    // e isso é sucesso, não uma falha — sem o gate o guard confundiria as
    // duas situações. `would_download` também conta: em dryRun os itens
    // bem-sucedidos pousam ali em vez de em `downloaded`, e sem essa conta um
    // dry run saudável sobre datas novas pareceria zero rendimento.
    const touched = summary.downloaded.length + summary.skipped.length + summary.would_download.length;
    const anyFailure = summary.errors.length > 0;
    if (deps.publications.length > 0 && touched === 0 && anyFailure) {
        summary.type = 'error';
        summary.durationMs = deps.now() - startedAt;
        // Anexa o summary ao erro: um run que falhou por inteiro ainda coletou
        // entradas de stage 'download' e 'dedup' pelo caminho, e descarta-las
        // so porque o run inteiro nao rendeu nada jogaria fora o diagnostico
        // exato de que o chamador vai precisar.
        const error = new ZeroYieldError(
            `Zero yield for the whole run: ${deps.publications.length} publication(s) processed, but nothing was `
            + 'downloaded and nothing was skipped as already-stored. Every publication either failed to list or '
            + 'produced nothing usable.',
        );
        error.summary = summary;
        throw error;
    }

    summary.durationMs = deps.now() - startedAt;
    return summary;
}
