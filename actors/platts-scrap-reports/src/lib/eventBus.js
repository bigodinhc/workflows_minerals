// Event bus: emits structured workflow events to stdout and the Supabase event_log.
// Never raises — sink failures are logged to stderr and swallowed so the actor
// never fails because of telemetry.
//
// Mirrors the Python EventBus contract in execution/core/event_bus.py so events
// from actors and cron scripts share the same trace_id / parent_run_id schema.
//
// Keep this file in sync between actors/platts-scrap-reports/src/lib/eventBus.js
// and actors/platts-scrap-full-news/src/lib/eventBus.js (Apify package isolation
// means we can't share via symlink or local import without breaking the Docker
// build). Changes to either copy should be mirrored to the other in the same PR.

import { createClient } from '@supabase/supabase-js';
import crypto from 'crypto';

const VALID_LEVELS = new Set(['info', 'warn', 'error']);

function generateRunId() {
    return crypto.randomBytes(4).toString('hex');
}

function initSupabase() {
    const url = process.env.SUPABASE_URL;
    const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
    if (!url || !key) return null;
    try {
        return createClient(url, key);
    } catch (err) {
        console.warn('EventBus: supabase init failed:', err.message);
        return null;
    }
}

export class EventBus {
    constructor({ workflow, traceId, parentRunId } = {}) {
        if (!workflow) {
            throw new Error('EventBus: workflow is required');
        }
        this._workflow = workflow;
        this._runId = generateRunId();
        this._traceId = traceId ?? this._runId;
        this._parentRunId = parentRunId ?? null;
        this._supabase = initSupabase();
    }

    get runId() { return this._runId; }
    get traceId() { return this._traceId; }

    async emit(event, { label = null, detail = null, level = 'info' } = {}) {
        if (!VALID_LEVELS.has(level)) level = 'info';
        const row = {
            workflow: this._workflow,
            run_id: this._runId,
            trace_id: this._traceId,
            parent_run_id: this._parentRunId,
            level,
            event,
            label,
            detail,
        };
        // Stdout sink — always fires (surfaces in Apify run logs).
        console.log(JSON.stringify({ ts: new Date().toISOString(), ...row }));
        // Supabase sink — best-effort.
        if (this._supabase) {
            try {
                await this._supabase.from('event_log').insert(row);
            } catch (err) {
                console.warn('EventBus: event_log insert failed:', err.message);
            }
        }
    }
}
