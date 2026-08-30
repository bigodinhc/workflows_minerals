import { describe, expect, it } from 'vitest';

import { describeSupabaseError } from '../src/persist/describeSupabaseError.js';

describe('describeSupabaseError', () => {
    it('keeps a message that is already usable', () => {
        expect(describeSupabaseError({ message: 'Bucket not found' })).toContain('Bucket not found');
    });

    // The one that matters: two backfill runs failed with an empty message and
    // logged "Storage upload failed: <none>". Whatever else changes, the
    // description must never come back blank.
    it('still describes an error whose message is blank', () => {
        const described = describeSupabaseError({ name: 'StorageApiError', message: '', status: 546 });

        expect(described).not.toBe('');
        expect(described).toContain('546');
        expect(described).toContain('StorageApiError');
    });

    it('surfaces the wrapped original error', () => {
        const described = describeSupabaseError({ message: '', originalError: new Error('fetch failed') });

        expect(described).toContain('fetch failed');
    });

    it('surfaces postgrest code, details and hint', () => {
        const described = describeSupabaseError({
            message: 'duplicate key',
            code: '23505',
            details: 'Key (slug, date_key) already exists.',
            hint: null,
        });

        expect(described).toContain('23505');
        expect(described).toContain('already exists');
    });

    it('falls back to the serialized object when nothing is named', () => {
        expect(describeSupabaseError({ weird: 'shape' })).toContain('weird');
    });

    it('never returns an empty string, whatever it is handed', () => {
        for (const input of [null, undefined, '', {}, 0]) {
            expect(describeSupabaseError(input)).not.toBe('');
        }
    });
});
