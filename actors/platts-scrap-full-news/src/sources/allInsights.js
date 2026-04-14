/**
 * allInsights Ferrous Metals — padrão A (paralelizável).
 *
 * Três coletores:
 * - collectFlashBanner: captura texto + timestamp do banner FLASH (breaking news)
 * - collectTopNewsList: slider de Top News (usa #platts-topNews-slider)
 * - collectLatestList: lista infinita (#platts-latest-news-infinite-list)
 */

import { closePopups } from '../auth/login.js';

const ALL_INSIGHTS_URL = 'https://core.spglobal.com/#platts/allInsights?keySector=Ferrous%20Metals';

export async function navigateToFerrousMetals(page, pageLog) {
    try {
        pageLog.info('🧭 Navegando para Ferrous Metals (allInsights)...');

        await page.goto(ALL_INSIGHTS_URL, {
            waitUntil: 'domcontentloaded', timeout: 30000,
        });
        await closePopups(page);

        // Espera qualquer container principal aparecer (slider OU latest list)
        try {
            await page.waitForSelector(
                '#platts-topNews-slider, #platts-latest-news-infinite-list',
                { timeout: 25000 },
            );
            pageLog.info('   ✅ allInsights carregada');
        } catch (e) {
            pageLog.warning('   ⚠️ Containers principais não apareceram em 25s, prosseguindo');
        }

        return true;
    } catch (error) {
        pageLog.error(`Erro Ferrous Metals: ${error.message}`);
        return false;
    }
}

/**
 * Captura o banner FLASH (breaking news).
 * Retorna array com 0 ou 1 item: { source: 'allInsights.flash', isFlash: true, title, date, fullText }.
 */
export async function collectFlashBanner(page, pageLog) {
    try {
        const flash = await page.evaluate(() => {
            const container = document.getElementById('platts-news-insight-flash-content') ||
                document.getElementById('news-insight-flash-section');
            if (!container) return null;

            const text = (container.innerText || '').trim();
            if (!text) return null;

            // Tenta separar timestamp + texto (ex: "20/02/2026 15:09:42 UTC: Supreme Court...")
            const match = text.match(/^(\d{2}\/\d{2}\/\d{4}\s+\d{2}:\d{2}:\d{2}\s+UTC):\s*(.*)$/s);
            if (match) {
                return {
                    date: match[1],
                    title: match[2].trim().slice(0, 300),
                    fullText: text,
                };
            }

            return { date: '', title: text.slice(0, 200), fullText: text };
        });

        if (!flash) {
            pageLog.info('   (sem FLASH ativo)');
            return [];
        }

        pageLog.info(`⚡ FLASH: ${flash.date} — ${flash.title.slice(0, 80)}`);
        return [{
            source: 'allInsights.flash',
            isFlash: true,
            title: flash.title,
            date: flash.date,
            gridDateTime: flash.date,
            publishDate: flash.date,
            actualDate: flash.date,
            fullText: flash.fullText,
            paragraphs: [flash.fullText],
            metadata: { wordCount: flash.fullText.split(/\s+/).filter(Boolean).length },
            extractedAt: new Date().toISOString(),
        }];
    } catch (error) {
        pageLog.error(`Erro FLASH: ${error.message}`);
        return [];
    }
}

export async function collectTopNewsList(page, pageLog, maxToCheck = 5) {
    try {
        pageLog.info(`⭐ Coletando Top News (verificar até ${maxToCheck})...`);

        await closePopups(page);
        await page.waitForSelector('#platts-topNews-slider a[href*="insightsArticle"]', { timeout: 10000 })
            .catch(() => pageLog.warning('   ⚠️ Links do slider não carregaram em 10s'));

        const topNewsList = await page.evaluate((max) => {
            const slider = document.getElementById('platts-topNews-slider');
            if (!slider) return [];

            const links = slider.querySelectorAll('a[href*="insightsArticle"]');
            const news = [];

            links.forEach((link, index) => {
                if (index < max) {
                    news.push({
                        index,
                        title: link.textContent.trim().substring(0, 200),
                        href: link.href || '',
                        date: '',
                        source: 'Top News - Ferrous Metals',
                        clickMethod: 'href',
                    });
                }
            });

            return news;
        }, maxToCheck);

        pageLog.info(`   📋 ${topNewsList.length} Top News para verificar`);
        return topNewsList;
    } catch (error) {
        pageLog.error(`Erro Top News: ${error.message}`);
        return [];
    }
}

/**
 * Latest list (infinite scroll). Pega até `maxItems` links visíveis.
 * Opcionalmente faz scroll pra carregar mais (se `scrollToLoad: true`).
 */
export async function collectLatestList(page, pageLog, maxItems = 30, scrollToLoad = false) {
    try {
        pageLog.info(`📰 Coletando Latest (max ${maxItems})...`);

        await closePopups(page);
        await page.waitForSelector('#platts-latest-news-infinite-list', { timeout: 15000 })
            .catch(() => pageLog.warning('   ⚠️ Latest list não apareceu em 15s'));

        if (scrollToLoad) {
            // Scroll pra baixo na lista pra disparar infinite scroll
            const maxScrollAttempts = 5;
            for (let attempt = 0; attempt < maxScrollAttempts; attempt++) {
                const currentCount = await page.evaluate(() => {
                    const list = document.getElementById('platts-latest-news-infinite-list');
                    return list ? list.querySelectorAll('a[href*="insightsArticle"]').length : 0;
                });
                if (currentCount >= maxItems) break;

                await page.evaluate(() => {
                    const list = document.getElementById('platts-latest-news-infinite-list');
                    if (list) list.scrollTop = list.scrollHeight;
                    window.scrollTo(0, document.body.scrollHeight);
                });
                await page.waitForTimeout(1500);
            }
        }

        const latestList = await page.evaluate((max) => {
            const list = document.getElementById('platts-latest-news-infinite-list');
            if (!list) return [];

            const links = [...list.querySelectorAll('a[href*="insightsArticle"]')];
            return links.slice(0, max).map((link, index) => {
                // Tenta pegar data do irmão/pai próximo
                let date = '';
                const container = link.closest('[class*="card"], [class*="item"], li, article, div');
                if (container) {
                    const text = container.innerText || '';
                    const m = text.match(/\d{2}\/\d{2}\/\d{4}\s+\d{2}:\d{2}:\d{2}\s+UTC/) ||
                        text.match(/há\s+\d+\s+\w+/i) ||
                        text.match(/\d+\s+\w+\s+ago/i);
                    if (m) date = m[0];
                }

                return {
                    index,
                    title: link.textContent.trim().substring(0, 200),
                    href: link.href || '',
                    date,
                    source: 'Latest',
                    clickMethod: 'href',
                };
            });
        }, maxItems);

        pageLog.info(`   📋 ${latestList.length} itens Latest`);
        return latestList;
    } catch (error) {
        pageLog.error(`Erro Latest: ${error.message}`);
        return [];
    }
}
