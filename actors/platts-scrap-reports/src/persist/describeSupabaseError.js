/**
 * Turn a Supabase error into something a human can act on.
 *
 * `error.message` comes back blank often enough to matter — the storage client
 * leaves it empty when the failure carries no JSON body — and the interpolated
 * string then reads "Storage upload failed: undefined". That was the single
 * recurring error of the 2024 and 2025 backfills, and the one nobody could
 * read. Same failure shape the Telegram client had before it started reading
 * the response body.
 *
 * Verbosity is the point: this runs only on a path that already failed, so
 * naming everything the object carries costs nothing and is the whole cure.
 */
export function describeSupabaseError(error) {
    if (error === null || error === undefined) return '(no error object)';
    if (typeof error !== 'object') {
        const text = String(error).trim();
        return text || `(${typeof error} ${JSON.stringify(error)})`;
    }

    const parts = [];

    const message = typeof error.message === 'string' ? error.message.trim() : '';
    if (message) parts.push(message);

    for (const key of ['name', 'code', 'status', 'statusCode']) {
        const value = error[key];
        if (value !== undefined && value !== null && value !== '') parts.push(`${key}=${value}`);
    }

    for (const key of ['details', 'hint']) {
        const value = error[key];
        if (typeof value === 'string' && value.trim()) parts.push(`${key}=${value.trim()}`);
    }

    const { originalError } = error;
    if (originalError) {
        const inner = typeof originalError.message === 'string'
            ? originalError.message.trim()
            : String(originalError).trim();
        if (inner) parts.push(`originalError=${inner}`);
    }

    if (parts.length > 0) return parts.join(' | ');

    // Nothing named was usable. Dump whatever the object carries rather than
    // returning an empty string — an empty string is what made this invisible.
    try {
        const dumped = JSON.stringify(error);
        if (dumped && dumped !== '{}') return dumped;
    } catch {
        // circular or otherwise unserialisable; fall through
    }
    return `(unreadable error object: ${Object.prototype.toString.call(error)})`;
}
