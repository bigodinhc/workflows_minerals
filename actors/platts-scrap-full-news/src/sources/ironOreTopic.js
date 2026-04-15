import { closePopups } from '../auth/login.js';
import { isDateWithinFilter } from '../util/dates.js';
import { saveDebugArtifacts } from '../util/debug.js';

const IRON_ORE_URL = 'https://core.spglobal.com/#platts/topic?menuserviceline=Ferrous%20Metals&serviceline=Steel%20%26%20Raw%20Materials&topic=Iron%20Ore';

export async function navigateToIronOre(page, pageLog) {
    try {
        pageLog.info('🧭 Navegando para Iron Ore...');

        // Se estamos em outro domínio, navega pro root do core primeiro pra inicializar SPA
        if (!page.url().startsWith('https://core.spglobal.com/')) {
            pageLog.info('   Inicializando core.spglobal.com root...');
            await page.goto('https://core.spglobal.com/', { waitUntil: 'domcontentloaded', timeout: 30000 });
            await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
        }

        await page.goto(IRON_ORE_URL, {
            waitUntil: 'domcontentloaded', timeout: 30000,
        });

        // Espera qualquer widget das 2 seções aparecer
        try {
            await page.waitForSelector(
                '#news-insights-title-0, #market-commentary-title-0',
                { timeout: 25000 },
            );
        } catch (e) {
            pageLog.warning('   ⚠️ Widgets da Iron Ore page não apareceram em 25s');
            await saveDebugArtifacts(page, 'ironore-timeout', {
                attemptedUrl: IRON_ORE_URL,
                finalUrl: page.url(),
            });
            pageLog.warning('   📸 Screenshot + HTML salvos em debug-ironore-timeout-*');
        }
        await closePopups(page);

        const info = await page.evaluate(() => {
            let count = 0;
            for (let i = 0; i < 10; i++) {
                if (document.getElementById(`news-insights-title-${i}`)) count++;
            }
            return { newsCount: count };
        });

        pageLog.info(`   Notícias: ${info.newsCount}`);
        return true;
    } catch (error) {
        pageLog.error(`Erro Iron Ore: ${error.message}`);
        return false;
    }
}

export async function collectNewsList(page, pageLog, maxArticles, dateFilter, daysBack, targetDate) {
    try {
        pageLog.info('📰 Coletando News & Insights...');

        await closePopups(page);
        await page.waitForSelector('#news-insights-title-0', { timeout: 15000 })
            .catch(() => pageLog.warning('   ⚠️ news-insights-title-0 não apareceu em 15s'));

        const newsList = await page.evaluate((sourceUrl) => {
            const news = [];
            for (let i = 0; i < 20; i++) {
                const el = document.getElementById(`news-insights-title-${i}`);
                if (el) {
                    const tsEl = document.getElementById(`newsinsights-timestamp-${i}`);
                    // Tenta capturar href direto (se o anchor tiver)
                    const href = el.href && !el.href.endsWith('#') && !el.href.endsWith('javascript:void(0)')
                        ? el.href
                        : null;
                    news.push({
                        index: i,
                        title: el.textContent.trim(),
                        date: tsEl?.textContent?.trim() || '',
                        href: href || '',
                        elementId: `news-insights-title-${i}`,
                        sourcePageUrl: sourceUrl,
                        source: 'News & Insights',
                        clickMethod: href ? 'href' : 'elementId',
                    });
                } else break;
            }
            return news;
        }, IRON_ORE_URL);

        pageLog.info(`   📋 ${newsList.length} encontrados`);

        newsList.forEach((item, i) => {
            pageLog.info(`   [${i}] "${item.title.substring(0, 40)}..." | Data: "${item.date}"`);
        });

        const filtered = [];
        for (const item of newsList) {
            if (isDateWithinFilter(item.date, dateFilter, daysBack, targetDate)) {
                filtered.push(item);
                if (filtered.length >= maxArticles) break;
            }
        }

        pageLog.info(`✅ ${filtered.length} passaram pelo filtro`);
        return filtered;
    } catch (error) {
        pageLog.error(`Erro News: ${error.message}`);
        return [];
    }
}

export async function collectMarketCommentaryList(page, pageLog, maxArticles, dateFilter, daysBack, targetDate) {
    try {
        pageLog.info('📊 Coletando Market Commentary (Iron Ore page)...');

        await page.waitForSelector('#market-commentary-title-0', { timeout: 15000 })
            .catch(() => pageLog.warning('   ⚠️ market-commentary-title-0 não apareceu em 15s'));

        const marketList = await page.evaluate((sourceUrl) => {
            const items = [];
            for (let i = 0; i < 10; i++) {
                const el = document.getElementById(`market-commentary-title-${i}`);
                if (el) {
                    const tsEl = document.getElementById(`market-commentary-timestamp-${i}`);
                    let date = tsEl?.textContent?.trim() || '';

                    if (!date) {
                        const container = el.parentElement;
                        if (container) {
                            const text = container.innerText || '';
                            const match = text.match(/\d{2}\/\d{2}\/\d{4}\s+\d{2}:\d{2}:\d{2}\s+UTC/);
                            if (match) date = match[0];
                        }
                    }

                    const href = el.href && !el.href.endsWith('#') && !el.href.endsWith('javascript:void(0)')
                        ? el.href
                        : null;

                    items.push({
                        index: i,
                        title: el.textContent.trim(),
                        date,
                        href: href || '',
                        elementId: `market-commentary-title-${i}`,
                        sourcePageUrl: sourceUrl,
                        source: 'Market Commentary',
                        clickMethod: href ? 'href' : 'elementId',
                    });
                } else break;
            }
            return items;
        }, IRON_ORE_URL);

        pageLog.info(`   📋 ${marketList.length} encontrados`);

        marketList.forEach((item, i) => {
            pageLog.info(`   [${i}] "${item.title.substring(0, 40)}..." | Data: "${item.date}"`);
        });

        const filtered = [];
        for (const item of marketList) {
            if (!item.date) {
                pageLog.warning(`   ⚠️ Sem data - pulando: "${item.title.substring(0, 30)}..."`);
                continue;
            }
            if (isDateWithinFilter(item.date, dateFilter, daysBack, targetDate)) {
                filtered.push(item);
                if (filtered.length >= maxArticles) break;
            }
        }

        pageLog.info(`✅ ${filtered.length} passaram pelo filtro`);
        return filtered;
    } catch (error) {
        pageLog.error(`Erro Market: ${error.message}`);
        return [];
    }
}
