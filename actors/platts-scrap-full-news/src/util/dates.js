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

export function parsePlattsDate(dateString) {
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
            day = first;
            month = second - 1;
        } else if (second > 12) {
            month = first - 1;
            day = second;
        } else {
            // Ambíguo: assume MM/DD/YYYY (formato servidor americano)
            month = first - 1;
            day = second;
        }

        return new Date(Date.UTC(year, month, day, hour, minute, secs));
    } catch (error) {
        return null;
    }
}

export function isDateWithinFilter(dateString, filterType, daysBack = 1, targetDate = null) {
    const articleDate = parsePlattsDate(dateString);

    if (!articleDate) {
        if (dateString && (dateString.includes('há') || dateString.includes('ago'))) {
            return true;
        }
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
