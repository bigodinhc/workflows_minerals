/**
 * Market Commentary / Rationale — grid "blended" da página do Iron Ore topic.
 *
 * Depois do redesign do Platts (ago/2026), os conteúdos Market Commentary e
 * Rationale aparecem na própria página do topic (mesma URL do ironOreTopic)
 * num widget de tabs Ant Design com grids AG-Grid. O grid é alimentado pelo
 * endpoint `content-bff/v4/search/blendedsearch`, cuja resposta já traz o
 * artigo COMPLETO (campo `Body` em HTML) além de título, data, commodities.
 *
 * Estratégia: em vez de clicar/raspar o grid (frágil), interceptamos as
 * respostas do endpoint via `page.on('response')`:
 * 1. attachBlendedCapture() ANTES de navegar pro topic
 * 2. a carga inicial da página dispara a busca da tab ativa (Market Commentary)
 * 3. collectTopicCommentary() clica na tab Rationale pra disparar a segunda
 *    busca, espera, e monta os artigos direto do JSON capturado
 */

import { COMPANIES } from '../extract/companies.js';
import { isParsedDateWithinFilter, parsePlattsDate } from '../util/dates.js';

const BLENDED_ENDPOINT = '/search/blendedsearch';
const TARGET_TYPES = ['Market Commentary', 'Rationale'];

function firstString(...candidates) {
    for (const c of candidates) {
        if (typeof c === 'string' && c.trim()) return c.trim();
    }
    return '';
}

export function blendedItemId(item) {
    return firstString(
        item?.Id, item?.ID, item?.ArticleId, item?.ArticleID,
        item?.DocumentId, item?.ContentId, item?.Guid,
    ) || `${firstString(item?.Headline, item?.Title)}|${firstString(item?.DisplayDate, item?.PublishedDate)}`;
}

/**
 * Registra o interceptor de respostas do blendedsearch. Chamar ANTES de
 * navegar pra página do topic. Acumula (dedup por id) só os items dos
 * ContentTypes alvo — o mesmo endpoint também serve Market Reports etc.
 */
export function attachBlendedCapture(page, pageLog) {
    const itemsById = new Map();
    let responses = 0;

    const handler = async (res) => {
        if (!res.url().includes(BLENDED_ENDPOINT)) return;
        let body;
        try {
            body = await res.json();
        } catch {
            return; // resposta não-JSON (preflight/erro) — ignora
        }
        const items = Array.isArray(body?.Items) ? body.Items : null;
        if (!items) return;
        responses++;
        for (const item of items) {
            const type = firstString(item?.ContentType, item?.ContentTypeValue);
            if (!TARGET_TYPES.includes(type)) continue;
            const key = blendedItemId(item);
            if (!itemsById.has(key)) itemsById.set(key, item);
        }
        pageLog?.info(`   🪝 blendedsearch #${responses}: ${items.length} items na resposta, ${itemsById.size} MC/Rationale acumulados`);
    };

    page.on('response', handler);
    return {
        items: () => [...itemsById.values()],
        size: () => itemsById.size,
        detach: () => page.off('response', handler),
    };
}

/** Converte o HTML do campo Body em texto plano com parágrafos. */
export function htmlToText(html) {
    if (!html) return '';
    const text = String(html)
        .replace(/<br\s*\/?>/gi, '\n')
        .replace(/<\/(p|div|h[1-6]|li|tr)>/gi, '\n\n')
        .replace(/<[^>]+>/g, '')
        .replace(/&nbsp;/gi, ' ')
        .replace(/&amp;/gi, '&')
        .replace(/&lt;/gi, '<')
        .replace(/&gt;/gi, '>')
        .replace(/&quot;/gi, '"')
        .replace(/&#0?39;|&apos;/gi, "'")
        .replace(/[ \t]+\n/g, '\n');
    return text.replace(/\n{3,}/g, '\n\n').trim();
}

/** Parseia a data do item: ISO 8601 primeiro, senão formato Platts. */
export function parseBlendedDate(raw) {
    if (!raw) return null;
    if (/^\d{4}-\d{2}-\d{2}/.test(raw)) {
        const d = new Date(raw);
        return Number.isNaN(d.getTime()) ? null : d;
    }
    return parsePlattsDate(raw);
}

function buildMetadata(fullText) {
    const metadata = {
        wordCount: fullText.split(/\s+/).filter((w) => w).length,
    };

    const priceMatches = fullText.match(/\$[\d,]+\.?\d*/g);
    if (priceMatches) metadata.prices = [...new Set(priceMatches)];

    const yuanMatches = fullText.match(/Yuan\s*[\d,]+\.?\d*/gi);
    if (yuanMatches) metadata.yuanPrices = [...new Set(yuanMatches)];

    const percentMatches = fullText.match(/[\d]+\.?\d*%/g);
    if (percentMatches) metadata.percentages = [...new Set(percentMatches)];

    metadata.companies = COMPANIES.filter((c) =>
        fullText.toLowerCase().includes(c.toLowerCase()),
    );

    const iodexMatch = fullText.match(/IODEX at \$([\d.]+)/);
    if (iodexMatch) metadata.iodexPrice = iodexMatch[1];

    const iopexMatches = fullText.match(/IOPEX[^.]+/g);
    if (iopexMatches) metadata.iopexAssessments = iopexMatches;

    const lumpMatch = fullText.match(/lump premium at ([\d.]+ cents\/dmtu)/i);
    if (lumpMatch) metadata.lumpPremium = lumpMatch[1];

    return metadata;
}

/**
 * Mapeia um item do blendedsearch pro shape de artigo do actor
 * (compatível com os artigos do RMW/padrão A no dataset final).
 */
export function blendedItemToArticle(item) {
    const type = firstString(item?.ContentType, item?.ContentTypeValue) || 'Market Commentary';
    const title = firstString(
        item?.Headline, item?.HeadLine, item?.Title, item?.DocumentTitle,
        (item?.Content || '').split('\n')[0].slice(0, 200),
    );
    const rawDate = firstString(
        item?.DisplayDate, item?.PublishedDate, item?.PublishDate,
        item?.UpdatedDate, item?.ModifiedDate, item?.Date, item?.CreatedDate,
    );
    const articleId = blendedItemId(item);
    const fullText = htmlToText(item?.Body) || firstString(item?.Content);
    const paragraphs = fullText.split(/\n\n+/).filter((p) => p.trim());

    return {
        source: `topic.${type.replace(/\s+/g, '')}`,
        contentType: type,
        articleId,
        href: `https://core.spglobal.com/#platts/insightsArticle?articleID=${encodeURIComponent(articleId)}&insightsType=${encodeURIComponent(type)}&showRelatedNews=true`,
        title,
        publishDate: rawDate,
        author: firstString(item?.Author),
        commodities: Array.isArray(item?.Commodity) ? item.Commodity : [],
        geographies: Array.isArray(item?.Geography) ? item.Geography : [],
        relatedDataSets: Array.isArray(item?.RelatedDataSet) ? item.RelatedDataSet : [],
        paragraphs,
        fullText,
        metadata: buildMetadata(fullText),
    };
}

async function dismissCookieBanner(page) {
    await page.evaluate(() => {
        const btn = [...document.querySelectorAll('button')]
            .find((b) => /^accept all$/i.test((b.innerText || '').trim()));
        btn?.click();
    }).catch(() => {});
}

/**
 * Os widgets da página do topic são deferred (ResponsiveGridLayout monta cada
 * widget só quando entra no viewport). O grid MC/Rationale fica abaixo do fold,
 * então em headless precisamos rolar até as tabs montarem no DOM.
 */
async function scrollUntilTabsMounted(page, pageLog, maxSteps = 18) {
    await page.mouse.move(400, 300).catch(() => {});
    for (let i = 0; i < maxSteps; i++) {
        const found = await page.evaluate(() => {
            const tabs = [...document.querySelectorAll('.ant-tabs-tab')]
                .filter((t) => t.offsetParent !== null);
            return tabs.some((t) => /market commentary|rationale/i.test(t.innerText || ''));
        }).catch(() => false);
        if (found) {
            pageLog.info(`   🌊 Tabs MC/Rationale montadas após ${i} scroll(s)`);
            return true;
        }
        await page.mouse.wheel(0, 900).catch(() => {});
        await page.waitForTimeout(700);
    }
    pageLog.warning(`   ⚠️ Tabs MC/Rationale não montaram após ${maxSteps} scrolls`);
    return false;
}

async function clickAntTab(page, tabNameRegex) {
    return page.evaluate((pattern) => {
        const re = new RegExp(pattern, 'i');
        const tab = [...document.querySelectorAll('.ant-tabs-tab')]
            .filter((t) => t.offsetParent !== null)
            .find((t) => re.test((t.innerText || '').trim()));
        if (!tab) return false;
        tab.click();
        return true;
    }, tabNameRegex);
}

async function waitForCaptureGrowth(page, capture, previousSize, timeoutMs = 8000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        if (capture.size() > previousSize) return true;
        await page.waitForTimeout(250);
    }
    return false;
}

/**
 * Coleta Market Commentary + Rationale a partir do capture. A page deve já
 * estar na página do Iron Ore topic (a carga inicial popula a tab ativa).
 * Clica na tab Rationale pra disparar a busca da segunda tab.
 */
export async function collectTopicCommentary(page, pageLog, capture, options = {}) {
    const { maxArticles = 10, dateFilter = 'today', daysToCollect = 1, targetDate = null } = options;

    const originalViewport = page.viewportSize();
    try {
        // Viewport maior ajuda o lazy-mount dos widgets deferred
        await page.setViewportSize({ width: 1600, height: 1000 }).catch(() => {});
        await dismissCookieBanner(page);

        // Rola até o widget de tabs montar — a montagem dispara a busca da tab
        // ativa (Market Commentary) no blendedsearch
        const mounted = await scrollUntilTabsMounted(page, pageLog);
        if (mounted && capture.size() === 0) {
            await waitForCaptureGrowth(page, capture, 0, 10000);
        }

        const clicked = await clickAntTab(page, '^rationale$');
        if (clicked) {
            const before = capture.size();
            const grew = await waitForCaptureGrowth(page, capture, before);
            pageLog.info(`   🗂️ Tab Rationale clicada — ${grew ? 'busca capturada' : 'sem busca nova em 8s (pode já estar no cache do capture)'}`);
        } else {
            pageLog.warning('   ⚠️ Tab Rationale não encontrada na página do topic');
        }

        const rawItems = capture.items();
        if (rawItems.length === 0) {
            pageLog.warning('   ⚠️ Nenhum item Market Commentary/Rationale capturado do blendedsearch');
            return [];
        }

        // Log defensivo: se o schema da API mudar, isto mostra os campos reais
        const sample = rawItems[0];
        pageLog.info(`   🔎 Campos do item: ${Object.keys(sample).join(', ')}`);

        const articles = rawItems
            .map((item) => blendedItemToArticle(item))
            .filter((a) => {
                if (dateFilter === 'all') return true;
                const parsed = parseBlendedDate(a.publishDate);
                if (!parsed) return true; // sem data legível: melhor manter do que dropar em silêncio
                return isParsedDateWithinFilter(parsed, dateFilter, daysToCollect, targetDate);
            })
            .slice(0, maxArticles);

        pageLog.info(`   🎯 ${articles.length}/${rawItems.length} dentro do filtro (max ${maxArticles})`);
        return articles;
    } catch (e) {
        pageLog.error(`   ❌ Erro coletando topic commentary: ${e.message}`);
        return [];
    } finally {
        if (originalViewport) {
            await page.setViewportSize(originalViewport).catch(() => {});
        }
    }
}
