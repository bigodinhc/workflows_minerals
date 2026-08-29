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
