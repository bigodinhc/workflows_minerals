import { log } from 'crawlee';

export function formatDateBR(date) {
    if (!date || isNaN(date.getTime())) return 'DATA INVÁLIDA';
    const day = String(date.getUTCDate()).padStart(2, '0');
    const month = String(date.getUTCMonth() + 1).padStart(2, '0');
    const year = date.getUTCFullYear();
    return `${day}/${month}/${year}`;
}

export function parseRelativeTime(timeString) {
    if (!timeString) return null;

    const now = new Date();
    const lowerTime = timeString.toLowerCase().trim();

    const patterns = [
        { regex: /há\s+(\d+)\s+minuto/i, unit: 'minutes' },
        { regex: /há\s+(\d+)\s+hora/i, unit: 'hours' },
        { regex: /há\s+(\d+)\s+dia/i, unit: 'days' },
        { regex: /(\d+)\s+minute[s]?\s+ago/i, unit: 'minutes' },
        { regex: /(\d+)\s+hour[s]?\s+ago/i, unit: 'hours' },
        { regex: /(\d+)\s+day[s]?\s+ago/i, unit: 'days' },
    ];

    for (const pattern of patterns) {
        const match = lowerTime.match(pattern.regex);
        if (match) {
            const value = parseInt(match[1]);
            const resultDate = new Date(now);

            switch (pattern.unit) {
                case 'minutes': resultDate.setMinutes(resultDate.getMinutes() - value); break;
                case 'hours': resultDate.setHours(resultDate.getHours() - value); break;
                case 'days': resultDate.setDate(resultDate.getDate() - value); break;
            }

            return resultDate;
        }
    }

    return null;
}

/**
 * Detecta o formato de uma lista de datas Platts (DD/MM vs MM/DD) procurando
 * uma amostra inequívoca — só o dia pode ser > 12. Retorna 'DMY', 'MDY' ou null
 * (todas ambíguas, dia e mês ≤12). Use pra fixar o formato de um grid inteiro a
 * partir de uma única row inequívoca, em vez de adivinhar row a row.
 */
export function detectDateFormat(samples) {
    for (const s of samples || []) {
        const m = String(s ?? '').match(/(\d{2})\/(\d{2})\/(\d{4})/);
        if (!m) continue;
        const a = parseInt(m[1]);
        const b = parseInt(m[2]);
        if (a > 12) return 'DMY';
        if (b > 12) return 'MDY';
    }
    return null;
}

/**
 * Parseia "DD/MM/YYYY HH:MM:SS UTC" ou "MM/DD/YYYY HH:MM:SS UTC".
 * @param {string} dateString
 * @param {'auto'|'DMY'|'MDY'} format Dica de formato. 'auto' (default) auto-desambigua
 *   por componente >12 e, quando ambíguo, assume MM/DD (ver nota abaixo).
 */
export function parsePlattsDate(dateString, format = 'auto') {
    if (!dateString) return null;

    const relativeDate = parseRelativeTime(dateString);
    if (relativeDate) return relativeDate;

    try {
        const cleanDate = dateString.replace('UTC', '').trim();
        const parts = cleanDate.match(/(\d{2})\/(\d{2})\/(\d{4})\s+(\d{2}):(\d{2}):(\d{2})/);
        if (!parts) return null;

        const first = parseInt(parts[1]);
        const second = parseInt(parts[2]);
        const year = parseInt(parts[3]);
        const hour = parseInt(parts[4]);
        const minute = parseInt(parts[5]);
        const secs = parseInt(parts[6]);

        let day, month;
        if (first > 12) {
            // first > 12 → só pode ser o dia (DD/MM), independente da dica
            day = first;
            month = second - 1;
        } else if (second > 12) {
            // second > 12 → só pode ser o dia (MM/DD), independente da dica
            month = first - 1;
            day = second;
        } else if (format === 'DMY') {
            day = first;
            month = second - 1;
        } else {
            // Ambíguo (dia e mês ≤12) sem dica DMY: assume MM/DD/YYYY.
            // A sessão headless do Platts Connect renderiza MM/DD/YYYY — verificado em
            // produção pelo banner FLASH "02/20/2026" (20>12 ⇒ MM/DD) e por timestamps
            // de artigos do mesmo dia "06/11/2026" em 11/jun. O browser interativo do
            // usuário mostra DD/MM por preferência de perfil, que a sessão automatizada
            // não herda. Datas inequívocas (componente >12) acima já têm prioridade, e
            // detectDateFormat() permite fixar 'DMY' por grid quando o Platts mudar.
            month = first - 1;
            day = second;
        }

        return new Date(Date.UTC(year, month, day, hour, minute, secs));
    } catch (error) {
        return null;
    }
}

export function isDateWithinFilter(dateString, filterType, daysBack = 1, targetDate = null, format = 'auto') {
    const articleDate = parsePlattsDate(dateString, format);

    if (!articleDate) {
        if (dateString && (dateString.includes('há') || dateString.includes('ago'))) {
            return true;
        }
        return filterType === 'all';
    }

    return isParsedDateWithinFilter(articleDate, filterType, daysBack, targetDate);
}

/**
 * Comparador do filtro de data para um Date já parseado. Permite fontes cujas
 * datas não vêm no formato Platts DD/MM|MM/DD (ex: ISO 8601 da API blendedsearch)
 * usarem a MESMA semântica de filtro (today/specificDate/lastXDays/all).
 */
export function isParsedDateWithinFilter(articleDate, filterType, daysBack = 1, targetDate = null) {
    if (!articleDate || Number.isNaN(articleDate.getTime())) {
        return filterType === 'all';
    }

    let targetDay;
    if (targetDate && (filterType === 'specificDate' || filterType === 'today')) {
        const parts = targetDate.match(/(\d{2})\/(\d{2})\/(\d{4})/);
        if (parts) {
            targetDay = new Date(Date.UTC(parseInt(parts[3]), parseInt(parts[2]) - 1, parseInt(parts[1])));
        }
    }

    if (!targetDay) {
        const now = new Date();
        targetDay = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
    }

    const articleDay = new Date(Date.UTC(
        articleDate.getUTCFullYear(),
        articleDate.getUTCMonth(),
        articleDate.getUTCDate(),
    ));

    switch (filterType) {
        case 'today':
        case 'specificDate':
            return articleDay.getTime() === targetDay.getTime();

        case 'lastXDays': {
            const cutoffDate = new Date(targetDay);
            cutoffDate.setUTCDate(cutoffDate.getUTCDate() - daysBack);
            return articleDay >= cutoffDate;
        }

        case 'all':
            return true;

        default:
            return true;
    }
}
