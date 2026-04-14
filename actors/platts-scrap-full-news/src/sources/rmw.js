/**
 * Raw Materials Workspace — padrão B (sequencial, reading pane).
 *
 * Estrutura da página:
 * - Tabs top-level: `page-widget-area-tab-{Iron Ore, Met Coal, ...}` (Iron Ore ativa default)
 * - Dentro da Iron Ore: tabela superior (prices) e inferior (news grid)
 * - Sub-tabs do grid de news: `2-widget-area-tab-{tabName}` (ex: IODEX Commentary and Rationale)
 * - Grid de news: rows com button.ag-anchor[role="link"]
 * - Ao clicar anchor: article abre em `.readingpane-details`
 */

import { closePopups } from '../auth/login.js';
import { extractReadingPaneContent } from '../extract/readingPane.js';
import { isDateWithinFilter } from '../util/dates.js';

const RMW_URL = 'https://core.spglobal.com/#platts/workspace?workspace=Raw%20Materials%20Workspace&type=public';

export async function navigateToRMW(page, pageLog) {
    try {
        pageLog.info('🧭 Navegando para Raw Materials Workspace...');
        await page.goto(RMW_URL, { waitUntil: 'domcontentloaded', timeout: 30000 });
        await closePopups(page);

        // Espera Iron Ore tab ativa e news grid carregar
        await page.waitForSelector('#page-widget-area-tab-Iron\\ Ore', { timeout: 30000 })
            .catch(() => pageLog.warning('   ⚠️ Tab Iron Ore não apareceu em 30s'));

        // Espera grid de news aparecer (10+ anchors)
        await page.waitForFunction(
            () => {
                const grids = document.querySelectorAll('.ag-root-wrapper');
                for (const g of grids) {
                    if (g.offsetParent === null) continue;
                    const anchors = g.querySelectorAll('button.ag-anchor, a.ag-anchor');
                    if (anchors.length >= 3) return true;
                }
                return false;
            },
            { timeout: 30000 },
        ).catch(() => pageLog.warning('   ⚠️ News grid com anchors não apareceu em 30s'));

        pageLog.info('   ✅ RMW pronta');
        return true;
    } catch (error) {
        pageLog.error(`Erro RMW navigate: ${error.message}`);
        return false;
    }
}

/**
 * Descobre todas as sub-tabs visíveis no widget-area "2" (tabela inferior de commentary).
 * Retorna [{ id, name }].
 */
export async function discoverCommentaryTabs(page, pageLog, filterRegex = '') {
    const tabs = await page.evaluate(() => {
        const hits = [...document.querySelectorAll('[id^="2-widget-area-tab-"]')]
            .filter((t) => t.offsetParent !== null);
        return hits.map((t) => ({ id: t.id, name: t.innerText?.trim() || '' }));
    });

    let filtered = tabs;
    if (filterRegex) {
        try {
            const re = new RegExp(filterRegex, 'i');
            filtered = tabs.filter((t) => re.test(t.name));
            pageLog.info(`   🎯 Filtro "${filterRegex}": ${filtered.length}/${tabs.length} tabs`);
        } catch (e) {
            pageLog.warning(`   ⚠️ Regex inválida "${filterRegex}", ignorando filtro`);
        }
    }

    pageLog.info(`   📑 Tabs encontradas: ${filtered.map((t) => t.name).join(' | ')}`);
    return filtered;
}

/**
 * Clica numa sub-tab e espera o grid atualizar.
 */
async function activateTab(page, pageLog, tab) {
    pageLog.info(`\n🗂️ Ativando tab: "${tab.name}"`);
    try {
        // Usa evaluate para escapar dos caracteres especiais no id
        const clicked = await page.evaluate((tabId) => {
            const el = document.getElementById(tabId);
            if (!el) return false;
            el.click();
            return true;
        }, tab.id);

        if (!clicked) {
            pageLog.warning(`   ⚠️ Tab ${tab.id} não encontrada`);
            return false;
        }

        // Espera grid repopular (detectar por mudança no número de rows ou conteúdo)
        await page.waitForTimeout(1500); // settle para re-render AG-Grid
        return true;
    } catch (e) {
        pageLog.error(`   ❌ Erro ativando tab: ${e.message}`);
        return false;
    }
}

/**
 * Lê as rows do grid visível que tem anchors (news grid).
 * Retorna [{ index, keyPage, title, date, anchorIndex, tabName, source, clickMethod }].
 */
async function collectGridArticles(page, pageLog, tabName) {
    const items = await page.evaluate((tabName) => {
        const grids = [...document.querySelectorAll('.ag-root-wrapper')];
        let newsGrid = null;
        for (const g of grids) {
            if (g.offsetParent === null) continue;
            if (g.querySelectorAll('button.ag-anchor, a.ag-anchor').length > 0) {
                newsGrid = g;
                break;
            }
        }
        if (!newsGrid) return [];

        const rows = [...newsGrid.querySelectorAll('.ag-row')];
        const anchors = [...newsGrid.querySelectorAll('button.ag-anchor, a.ag-anchor')];

        return rows.map((row, idx) => {
            const cells = [...row.querySelectorAll('.ag-cell')];
            const keyPage = cells[0]?.innerText?.trim() || '';
            const anchor = row.querySelector('button.ag-anchor, a.ag-anchor');
            const title = anchor?.innerText?.trim() || cells[1]?.innerText?.trim() || '';
            // Date cell (pode estar em posição 2 ou diferente)
            const dateCell = cells.find((c) => /\d{2}\/\d{2}\/\d{4}/.test(c.innerText || ''));
            const date = dateCell?.innerText?.trim() || '';
            const anchorIndex = anchor ? anchors.indexOf(anchor) : -1;

            return {
                index: idx,
                keyPage,
                title,
                date,
                anchorIndex,
                tabName,
                source: `rmw.${tabName}`,
                clickMethod: 'rmwAnchor',
            };
        }).filter((i) => i.title && i.anchorIndex >= 0);
    }, tabName);

    pageLog.info(`   📋 ${items.length} rows no grid`);
    return items;
}

/**
 * Clica no anchor pela posição (anchorIndex) e espera o reading pane atualizar.
 */
async function openArticleInPane(page, pageLog, item) {
    try {
        const clicked = await page.evaluate((anchorIndex) => {
            const grids = [...document.querySelectorAll('.ag-root-wrapper')];
            for (const g of grids) {
                if (g.offsetParent === null) continue;
                const anchors = [...g.querySelectorAll('button.ag-anchor, a.ag-anchor')];
                if (anchors.length > anchorIndex) {
                    anchors[anchorIndex].click();
                    return true;
                }
            }
            return false;
        }, item.anchorIndex);

        if (!clicked) {
            pageLog.warning(`   ⚠️ Anchor ${item.anchorIndex} não encontrado`);
            return false;
        }

        // Espera reading pane existir e ter o título esperado (confirma que atualizou)
        const titleKey = item.title.slice(0, 30).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        await page.waitForFunction(
            (expected) => {
                const pane = document.querySelector('.readingpane-details');
                if (!pane) return false;
                const paneText = (pane.innerText || '').slice(0, 500);
                return paneText.includes(expected);
            },
            titleKey.slice(0, 30),
            { timeout: 15000 },
        ).catch(() => {
            // Fallback: só espera o pane existir
            return page.waitForSelector('.readingpane-details', { timeout: 5000 }).catch(() => {});
        });

        return true;
    } catch (e) {
        pageLog.error(`   ❌ Erro abrindo artigo: ${e.message}`);
        return false;
    }
}

/**
 * Orquestra a extração completa de todas as tabs → todos os artigos.
 * Retorna [{ tabName, articles: [{...item, ...content}] }].
 */
export async function collectRMW(page, pageLog, options = {}) {
    const {
        maxArticlesPerTab = 10,
        dateFilter = 'all',
        daysToCollect = 1,
        targetDate = null,
        tabFilter = '',
    } = options;

    if (!await navigateToRMW(page, pageLog)) return [];

    const tabs = await discoverCommentaryTabs(page, pageLog, tabFilter);
    if (tabs.length === 0) {
        pageLog.warning('   ⚠️ Nenhuma sub-tab descoberta');
        return [];
    }

    const results = [];

    for (const tab of tabs) {
        if (!await activateTab(page, pageLog, tab)) continue;

        const items = await collectGridArticles(page, pageLog, tab.name);

        // Filtra por data se possível
        const filtered = items.filter((it) => {
            if (!it.date) return dateFilter === 'all';
            return isDateWithinFilter(it.date, dateFilter, daysToCollect, targetDate);
        }).slice(0, maxArticlesPerTab);

        pageLog.info(`   🎯 ${filtered.length} dentro do filtro`);

        const tabArticles = [];
        for (let i = 0; i < filtered.length; i++) {
            const item = filtered[i];
            pageLog.info(`\n   📖 [${i + 1}/${filtered.length}] ${item.title.slice(0, 60)}...`);

            const opened = await openArticleInPane(page, pageLog, item);
            if (!opened) continue;

            const content = await extractReadingPaneContent(page);
            if (!content || !content.fullText) {
                pageLog.warning('      ⚠️ Pane vazio, pulando');
                continue;
            }

            tabArticles.push({
                ...item,
                ...content,
                gridDateTime: item.date,
                extractedAt: new Date().toISOString(),
            });
            pageLog.info(`      ✅ ${content.metadata?.wordCount || 0} palavras${content.metadata?.iodexPrice ? ` | IODEX $${content.metadata.iodexPrice}` : ''}`);
        }

        results.push({ tabName: tab.name, tabId: tab.id, articles: tabArticles });
    }

    return results;
}
