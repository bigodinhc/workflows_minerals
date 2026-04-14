import { closePopups } from '../auth/login.js';
import { isDateWithinFilter } from '../util/dates.js';

const IRON_ORE_URL = 'https://core.spglobal.com/#platts/topic?menuserviceline=Ferrous%20Metals&serviceline=Steel%20%26%20Raw%20Materials&topic=Iron%20Ore';

export async function navigateToIronOre(page, pageLog) {
    try {
        pageLog.info('🧭 Navegando para Iron Ore...');

        await page.goto(IRON_ORE_URL, {
            waitUntil: 'domcontentloaded', timeout: 30000,
        });

        // Espera qualquer widget das 2 seções aparecer (em vez de wait fixo)
        await page.waitForSelector(
            '#news-insights-title-0, #market-commentary-title-0',
            { timeout: 20000 },
        ).catch(() => pageLog.warning('   ⚠️ Nenhum widget apareceu em 20s, continuando mesmo assim'));
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

        const newsList = await page.evaluate(() => {
            const news = [];
            for (let i = 0; i < 20; i++) {
                const el = document.getElementById(`news-insights-title-${i}`);
                if (el) {
                    const tsEl = document.getElementById(`newsinsights-timestamp-${i}`);
                    news.push({
                        index: i,
                        title: el.textContent.trim(),
                        date: tsEl?.textContent?.trim() || '',
                        elementId: `news-insights-title-${i}`,
                        source: 'News & Insights',
                        clickMethod: 'elementId',
                    });
                } else break;
            }
            return news;
        });

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

        const marketList = await page.evaluate(() => {
            const items = [];
            for (let i = 0; i < 10; i++) {
                const el = document.getElementById(`market-commentary-title-${i}`);
                if (el) {
                    const tsEl = document.getElementById(`market-commentary-timestamp-${i}`);
                    let date = tsEl?.textContent?.trim() || '';

                    if (!date) {
                        // Fallback: regex no container pai
                        const container = el.parentElement;
                        if (container) {
                            const text = container.innerText || '';
                            const match = text.match(/\d{2}\/\d{2}\/\d{4}\s+\d{2}:\d{2}:\d{2}\s+UTC/);
                            if (match) date = match[0];
                        }
                    }

                    items.push({
                        index: i,
                        title: el.textContent.trim(),
                        date,
                        elementId: `market-commentary-title-${i}`,
                        source: 'Market Commentary',
                        clickMethod: 'elementId',
                    });
                } else break;
            }
            return items;
        });

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
