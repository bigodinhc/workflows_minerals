import { describe, expect, it } from 'vitest';

import { describeFileFormat } from '../src/download/capturePdf.js';

const withHeader = (hex) => Buffer.concat([Buffer.from(hex, 'hex'), Buffer.alloc(64)]);

describe('describeFileFormat', () => {
    it('recognises a PDF', () => {
        expect(describeFileFormat(Buffer.from('%PDF-1.7\n...'))).toBe('PDF');
    });

    // The two headers that produced 15 "not a PDF" errors per run in July.
    it('names a modern Office/ZIP container instead of showing raw bytes', () => {
        expect(describeFileFormat(withHeader('504b0304'))).toMatch(/xlsx|zip/i);
    });

    it('names a legacy Office document', () => {
        expect(describeFileFormat(withHeader('d0cf11e0'))).toMatch(/legacy/i);
    });

    it('falls back to the hex header for anything unrecognised', () => {
        expect(describeFileFormat(withHeader('deadbeef'))).toContain('deadbeef');
    });

    it('flags an empty download', () => {
        expect(describeFileFormat(Buffer.alloc(0))).toMatch(/empty/i);
    });
});
