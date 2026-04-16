import { describe, expect,it } from 'vitest';

import { slugify } from '../src/util/slug.js';

describe('slugify', () => {
    it('lowercases and replaces spaces with hyphens', () => {
        expect(slugify('SBB Steel Markets Daily')).toBe('sbb-steel-markets-daily');
    });

    it('strips non-alphanumeric except hyphens', () => {
        expect(slugify('Global Market Outlook (Português)')).toBe('global-market-outlook-portugues');
    });

    it('collapses multiple spaces and special chars to single hyphens', () => {
        expect(slugify('  Steel  &  Iron — Daily  ')).toBe('steel-iron-daily');
    });

    it('removes leading/trailing hyphens', () => {
        expect(slugify('--Hello--')).toBe('hello');
    });

    it('handles empty string', () => {
        expect(slugify('')).toBe('');
    });

    it('handles accents (basic ASCII fold)', () => {
        expect(slugify('Análise Diária')).toBe('analise-diaria');
    });
});
