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
