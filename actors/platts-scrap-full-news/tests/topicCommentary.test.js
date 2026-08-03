import { describe, expect, it } from 'vitest';

import {
    attachBlendedCapture,
    blendedItemToArticle,
    htmlToText,
    parseBlendedDate,
    selectArticles,
} from '../src/sources/topicCommentary.js';

// Fixture baseada na resposta real do content-bff/v4/search/blendedsearch
// capturada em 03/08/2026 na página do Iron Ore topic
const FIXTURE_ITEM = {
    ContentType: 'Rationale',
    ContentTypeValue: 'Rationale',
    Content: 'Platts assessed the IODEX at $93.70/dry metric ton CFR North China Aug. 3...',
    Commodity: ['Pilbara Blend Fines', 'Iron Ore Fines', 'Iron Ore'],
    Geography: ['North China', 'Australia', 'China'],
    ImportantStory: '3',
    Author: 'Staff',
    RelatedDataSet: ['IODBZ00', 'TS01021'],
    Body: '<p>Platts assessed the IODEX at $93.70/dry metric ton CFR North China Aug. 3, down $1.60/dmt day over day, in line with a Pilbara Blend Fines trade, and in line with tradables values.</p>\n<p>At 4:23 pm Singapore time, Vale&#39;s offer was heard at Yuan 690/wmt.</p>',
    Headline: 'Platts IODEX rationale Aug 3',
    Id: 'd45a4fcb-ed30-4075-a718-a36e0ac6f59d',
    DisplayDate: '2026-08-03T12:25:00Z',
};

function fakePage() {
    const listeners = {};
    return {
        on: (event, handler) => { listeners[event] = handler; },
        off: (event) => { delete listeners[event]; },
        emit: (event, payload) => listeners[event]?.(payload),
    };
}

function fakeResponse(url, body) {
    return { url: () => url, json: async () => body };
}

describe('htmlToText', () => {
    it('converte parágrafos em texto com quebras duplas', () => {
        const text = htmlToText('<p>Primeiro parágrafo.</p>\n<p>Segundo parágrafo.</p>');
        expect(text).toBe('Primeiro parágrafo.\n\nSegundo parágrafo.');
    });

    it('decodifica entities comuns', () => {
        expect(htmlToText('<p>Vale&#39;s &amp; BHP&nbsp;&quot;deal&quot;</p>')).toBe('Vale\'s & BHP "deal"');
    });

    it('retorna vazio para input vazio', () => {
        expect(htmlToText('')).toBe('');
        expect(htmlToText(null)).toBe('');
    });
});

describe('parseBlendedDate', () => {
    it('parseia ISO 8601', () => {
        const d = parseBlendedDate('2026-08-03T12:25:00Z');
        expect(d.getUTCDate()).toBe(3);
        expect(d.getUTCMonth()).toBe(7);
    });

    it('parseia formato Platts com dia inequívoco', () => {
        const d = parseBlendedDate('20/02/2026 15:09:42 UTC');
        expect(d.getUTCDate()).toBe(20);
        expect(d.getUTCMonth()).toBe(1);
    });

    it('retorna null para lixo', () => {
        expect(parseBlendedDate('não é data')).toBeNull();
        expect(parseBlendedDate('')).toBeNull();
    });
});

describe('blendedItemToArticle', () => {
    it('mapeia o fixture real pro shape de artigo', () => {
        const a = blendedItemToArticle(FIXTURE_ITEM);
        expect(a.source).toBe('topic.Rationale');
        expect(a.contentType).toBe('Rationale');
        expect(a.articleId).toBe('d45a4fcb-ed30-4075-a718-a36e0ac6f59d');
        expect(a.href).toContain('articleID=d45a4fcb-ed30-4075-a718-a36e0ac6f59d');
        expect(a.href).toContain('insightsType=Rationale');
        expect(a.title).toBe('Platts IODEX rationale Aug 3');
        expect(a.publishDate).toBe('2026-08-03T12:25:00Z');
        expect(a.fullText).toContain('IODEX at $93.70');
        expect(a.fullText).toContain("Vale's offer");
        expect(a.paragraphs.length).toBe(2);
        expect(a.metadata.wordCount).toBeGreaterThan(20);
        expect(a.metadata.iodexPrice).toBe('93.70');
        expect(a.metadata.prices).toContain('$93.70');
        expect(a.commodities).toContain('Iron Ore');
    });

    it('cai pro Content quando Body está ausente', () => {
        const a = blendedItemToArticle({ ...FIXTURE_ITEM, Body: undefined });
        expect(a.fullText).toContain('IODEX at $93.70');
    });

    it('gera id sintético quando não há campo de id', () => {
        const a = blendedItemToArticle({ ...FIXTURE_ITEM, Id: undefined });
        expect(a.articleId).toContain('Platts IODEX rationale Aug 3');
    });
});

describe('attachBlendedCapture', () => {
    it('acumula MC/Rationale/News (não Reports), com dedup por id', async () => {
        const page = fakePage();
        const capture = attachBlendedCapture(page, null);

        await page.emit('response', fakeResponse(
            'https://api.platts.com/platts-platform/content-bff/v4/search/blendedsearch',
            {
                Items: [
                    FIXTURE_ITEM,
                    { ...FIXTURE_ITEM, Id: 'outro-id', ContentType: 'Market Commentary' },
                    { ...FIXTURE_ITEM, Id: 'news-id', ContentType: 'News' },
                    { ...FIXTURE_ITEM, Id: 'reports-id', ContentType: 'Market Reports' },
                ],
            },
        ));
        // resposta repetida (tab revisitada) não duplica
        await page.emit('response', fakeResponse(
            'https://api.platts.com/platts-platform/content-bff/v4/search/blendedsearch',
            { Items: [FIXTURE_ITEM] },
        ));
        // outra URL é ignorada
        await page.emit('response', fakeResponse(
            'https://api.platts.com/platts-platform/content-bff/v3/search/blendedtypes',
            { Items: [{ ...FIXTURE_ITEM, Id: 'nao-entra' }] },
        ));

        expect(capture.size()).toBe(3);
        const types = capture.items().map((i) => i.ContentType).sort();
        expect(types).toEqual(['Market Commentary', 'News', 'Rationale']);
        capture.detach();
    });

    it('ignora respostas não-JSON sem quebrar', async () => {
        const page = fakePage();
        const capture = attachBlendedCapture(page, null);
        await page.emit('response', {
            url: () => 'https://api.platts.com/x/search/blendedsearch',
            json: async () => { throw new Error('not json'); },
        });
        expect(capture.size()).toBe(0);
        capture.detach();
    });
});

describe('selectArticles', () => {
    const MIXED = [
        FIXTURE_ITEM,
        { ...FIXTURE_ITEM, Id: 'mc-1', ContentType: 'Market Commentary', Headline: 'Asian iron ore prices dip' },
        { ...FIXTURE_ITEM, Id: 'news-1', ContentType: 'News', Headline: 'Steel news of the day' },
        { ...FIXTURE_ITEM, Id: 'old-1', ContentType: 'Rationale', DisplayDate: '2026-07-31T10:00:00Z', Headline: 'Rationale antigo' },
    ];

    it('filtra por ContentType', () => {
        const arts = selectArticles(MIXED, ['News'], { dateFilter: 'all' });
        expect(arts.map((a) => a.title)).toEqual(['Steel news of the day']);
    });

    it('aplica sourceOverride', () => {
        const arts = selectArticles(MIXED, ['News'], { dateFilter: 'all', sourceOverride: 'News & Insights' });
        expect(arts[0].source).toBe('News & Insights');
    });

    it('aplica filtro de data por targetDate', () => {
        const arts = selectArticles(MIXED, ['Market Commentary', 'Rationale'], {
            dateFilter: 'specificDate', targetDate: '03/08/2026', maxArticles: 40,
        });
        expect(arts.map((a) => a.title)).toEqual(['Platts IODEX rationale Aug 3', 'Asian iron ore prices dip']);
    });

    it('respeita o teto maxArticles', () => {
        const arts = selectArticles(MIXED, ['Market Commentary', 'Rationale', 'News'], {
            dateFilter: 'all', maxArticles: 2,
        });
        expect(arts.length).toBe(2);
    });
});
