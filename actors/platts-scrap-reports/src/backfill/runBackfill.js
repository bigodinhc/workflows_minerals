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
    constructor(message) {
        super(message);
        this.name = 'ZeroYieldError';
    }
}

async function handleItem(row, deps, acc) {
    const slug = slugify(row.reportName);
    const parts = datePartsFromIso(row.dateKey);
    if (!slug || !parts) {
        acc.errors.push({ stage: 'parse-row', reportName: row.reportName, message: 'missing slug or dateKey' });
        acc.type = 'partial';
        return;
    }

    let alreadyStored;
    try {
        alreadyStored = await deps.isAlreadyStored(slug, row.dateKey);
    } catch (e) {
        acc.errors.push({ stage: 'dedup', reportName: row.reportName, message: e.message });
        acc.type = 'partial';
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
        acc.errors.push({ stage: 'download', reportName: row.reportName, message: e.message });
        acc.type = 'partial';
        return;
    }

    const format = describeFileFormat(buffer);
    if (format !== 'PDF') {
        acc.errors.push({ stage: 'download', reportName: row.reportName, message: `Downloaded file is ${format}` });
        acc.type = 'partial';
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
        acc.errors.push({ stage: 'supabase-upload', reportName: row.reportName, message: e.message });
        acc.type = 'partial';
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
            summary.errors.push({ stage: 'publication', publication, message: e.message });
            summary.type = 'partial';
        }
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
    // duas situações.
    const touched = summary.downloaded.length + summary.skipped.length;
    const anyFailure = summary.errors.length > 0;
    if (deps.publications.length > 0 && touched === 0 && anyFailure) {
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
