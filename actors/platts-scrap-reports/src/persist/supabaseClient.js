import { createClient } from '@supabase/supabase-js';
import { log } from 'crawlee';

let client = null;

export function getSupabase() {
    if (client) return client;
    const url = process.env.SUPABASE_URL;
    const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
    if (!url || !key) {
        throw new Error('SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY env vars are required');
    }
    log.info(`Supabase client initialized: ${url}`);
    client = createClient(url, key, {
        auth: { persistSession: false, autoRefreshToken: false },
    });
    return client;
}
