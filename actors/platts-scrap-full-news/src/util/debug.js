import { Actor } from 'apify';

/**
 * Salva screenshot (fullPage), HTML e meta do page no Key-Value Store.
 * Nunca lança — falhas de debug não podem matar o run.
 */
export async function saveDebugArtifacts(page, key, extraMeta = {}) {
    if (!page) return null;

    try {
        const safe = String(key).replace(/[^a-zA-Z0-9-_]/g, '_').substring(0, 80);
        const timestamp = Date.now();
        const prefix = `debug-${safe}-${timestamp}`;

        const png = await page.screenshot({ fullPage: true }).catch(() => null);
        if (png) await Actor.setValue(`${prefix}.png`, png, { contentType: 'image/png' });

        const html = await page.content().catch(() => null);
        if (html) await Actor.setValue(`${prefix}.html`, html, { contentType: 'text/html; charset=utf-8' });

        const url = page.url();
        await Actor.setValue(`${prefix}-meta.json`, { url, timestamp, key, ...extraMeta });

        return { prefix, url, timestamp };
    } catch (e) {
        return null;
    }
}
