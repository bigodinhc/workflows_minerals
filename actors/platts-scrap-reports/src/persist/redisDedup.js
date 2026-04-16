import { log } from 'crawlee';
import Redis from 'ioredis';

const SEEN_TTL_SECONDS = 90 * 24 * 60 * 60; // 90 days

let client = null;

function getClient() {
    if (client) return client;
    const url = process.env.REDIS_URL;
    if (!url) throw new Error('REDIS_URL env var is required for dedup');
    client = new Redis(url, {
        connectTimeout: 5000,
        commandTimeout: 5000,
        maxRetriesPerRequest: 1,
    });
    client.on('error', (err) => log.warning(`Redis error: ${err.message}`));
    return client;
}

export function seenKey(slug, dateKey) {
    return `platts:report:seen:${slug}:${dateKey}`;
}

export async function isSeen(slug, dateKey) {
    const r = getClient();
    const exists = await r.exists(seenKey(slug, dateKey));
    return exists === 1;
}

export async function markSeen(slug, dateKey) {
    const r = getClient();
    await r.set(seenKey(slug, dateKey), '1', 'EX', SEEN_TTL_SECONDS);
}

export async function closeRedis() {
    if (client) {
        await client.quit();
        client = null;
    }
}
