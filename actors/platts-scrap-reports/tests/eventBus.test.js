import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { EventBus } from '../src/lib/eventBus.js';

describe('EventBus', () => {
    let originalEnv;

    beforeEach(() => {
        originalEnv = { ...process.env };
        // Default: no Supabase creds, so _supabase is null
        delete process.env.SUPABASE_URL;
        delete process.env.SUPABASE_SERVICE_ROLE_KEY;
    });

    afterEach(() => {
        process.env = originalEnv;
    });

    it('throws when workflow is missing', () => {
        expect(() => new EventBus({})).toThrow(/workflow/i);
    });

    it('generates 8-char lowercase-hex runId', () => {
        const bus = new EventBus({ workflow: 'test' });
        expect(bus.runId).toMatch(/^[0-9a-f]{8}$/);
    });

    it('inherits traceId from constructor arg', () => {
        const bus = new EventBus({ workflow: 'test', traceId: 'abc12345', parentRunId: 'xyz98765' });
        expect(bus.traceId).toBe('abc12345');
    });

    it('defaults traceId to runId when none provided (new root trace)', () => {
        const bus = new EventBus({ workflow: 'test' });
        expect(bus.traceId).toBe(bus.runId);
    });

    it('defaults parentRunId to null when none provided', () => {
        const bus = new EventBus({ workflow: 'test' });
        const logSpy = vi.spyOn(console, 'log').mockImplementation(() => {});
        bus.emit('cron_started');
        const loggedJson = JSON.parse(logSpy.mock.calls[0][0]);
        expect(loggedJson.parent_run_id).toBeNull();
        logSpy.mockRestore();
    });

    it('emit writes one JSON line to stdout with all required fields', async () => {
        const logSpy = vi.spyOn(console, 'log').mockImplementation(() => {});
        const bus = new EventBus({ workflow: 'test_wf', traceId: 't1', parentRunId: 'p1' });
        await bus.emit('cron_started', { label: 'hello', detail: { a: 1 }, level: 'info' });

        expect(logSpy).toHaveBeenCalledTimes(1);
        const logged = JSON.parse(logSpy.mock.calls[0][0]);
        expect(logged.workflow).toBe('test_wf');
        expect(logged.event).toBe('cron_started');
        expect(logged.label).toBe('hello');
        expect(logged.level).toBe('info');
        expect(logged.detail).toEqual({ a: 1 });
        expect(logged.trace_id).toBe('t1');
        expect(logged.parent_run_id).toBe('p1');
        expect(logged.run_id).toMatch(/^[0-9a-f]{8}$/);
        expect(logged.ts).toMatch(/^\d{4}-\d{2}-\d{2}T/);
        logSpy.mockRestore();
    });

    it('emit coerces invalid level to info', async () => {
        const logSpy = vi.spyOn(console, 'log').mockImplementation(() => {});
        const bus = new EventBus({ workflow: 'test' });
        await bus.emit('custom', { level: 'critical' });
        const logged = JSON.parse(logSpy.mock.calls[0][0]);
        expect(logged.level).toBe('info');
        logSpy.mockRestore();
    });

    it('emit writes to supabase when _supabase is present', async () => {
        const insertMock = vi.fn().mockResolvedValue({});
        const fromMock = vi.fn().mockReturnValue({ insert: insertMock });
        const logSpy = vi.spyOn(console, 'log').mockImplementation(() => {});

        const bus = new EventBus({ workflow: 'test' });
        bus._supabase = { from: fromMock };
        await bus.emit('cron_started');

        expect(fromMock).toHaveBeenCalledWith('event_log');
        expect(insertMock).toHaveBeenCalledTimes(1);
        const insertedRow = insertMock.mock.calls[0][0];
        expect(insertedRow.workflow).toBe('test');
        expect(insertedRow.event).toBe('cron_started');
        // 'ts' must NOT be in the row — Supabase uses NOW() default
        expect(insertedRow.ts).toBeUndefined();
        logSpy.mockRestore();
    });

    it('emit is never-raise when supabase insert throws', async () => {
        const logSpy = vi.spyOn(console, 'log').mockImplementation(() => {});
        const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});

        const bus = new EventBus({ workflow: 'test' });
        bus._supabase = {
            from: () => ({ insert: () => Promise.reject(new Error('boom')) }),
        };
        await expect(bus.emit('cron_started')).resolves.not.toThrow();
        expect(warnSpy).toHaveBeenCalled();
        logSpy.mockRestore();
        warnSpy.mockRestore();
    });

    it('emit is never-raise when supabase client is null', async () => {
        const logSpy = vi.spyOn(console, 'log').mockImplementation(() => {});
        const bus = new EventBus({ workflow: 'test' });
        expect(bus._supabase).toBeNull();
        await expect(bus.emit('cron_started')).resolves.not.toThrow();
        logSpy.mockRestore();
    });
});
