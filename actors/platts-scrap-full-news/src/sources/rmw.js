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
import { saveDebugArtifacts } from '../util/debug.js';

const RMW_URL = 'https://core.spglobal.com/#platts/workspace?workspace=Raw%20Materials%20Workspace&type=public';

export async function navigateToRMW(page, pageLog) {
    try {
        if (!page.url().startsWith('https://core.spglobal.com/')) {
            pageLog.info('🧭 Inicializando core.spglobal.com root...');
            await page.goto('https://core.spglobal.com/', { waitUntil: 'domcontentloaded', timeout: 30000 });
            await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
        }

        pageLog.info('🧭 Navegando para Raw Materials Workspace...');
        await page.goto(RMW_URL, { waitUntil: 'domcontentloaded', timeout: 30000 });
        await closePopups(page);

        // Espera Iron Ore tab ativa e news grid carregar
        await page.waitForSelector('[id*="page-widget-area-tab-Iron"]', { timeout: 30000 })
            .catch(() => pageLog.warning('   ⚠️ Tab Iron Ore não apareceu em 30s'));

        // Espera grid de news aparecer (anchors)
        const gridReady = await page.waitForFunction(
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
        ).then(() => true).catch(() => false);

        if (!gridReady) {
            pageLog.warning('   ⚠️ News grid com anchors não apareceu em 30s');
            await saveDebugArtifacts(page, 'rmw-timeout', { attemptedUrl: RMW_URL, finalUrl: page.url() });
            pageLog.warning('   📸 Screenshot + HTML salvos em debug-rmw-timeout-*');
            return false;
        }

        pageLog.info('   ✅ RMW pronta');
        return true;
    } catch (error) {
        pageLog.error(`Erro RMW navigate: ${error.message}`);
        await saveDebugArtifacts(page, 'rmw-navigate-error', { error: error.message });
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
        // Marca grids atuais antes do click pra detectar re-render
        const preClickState = await page.evaluate(() => {
            const grids = [...document.querySelectorAll('.ag-root-wrapper')]
                .filter((g) => g.offsetParent !== null);
            const target = grids.find((g) =>
                g.querySelectorAll('button.ag-anchor, a.ag-anchor').length > 0,
            );
            return {
                rowCount: target ? target.querySelectorAll('.ag-row').length : 0,
                firstTitle: target?.querySelector('button.ag-anchor, a.ag-anchor')?.innerText?.trim() || '',
            };
        });

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

        // Espera grid repopular: ou firstTitle muda, ou rowCount muda, ou passa X ms
        await page.waitForFunction(
            (prev) => {
                const grids = [...document.querySelectorAll('.ag-root-wrapper')]
                    .filter((g) => g.offsetParent !== null);
                const target = grids.find((g) =>
                    g.querySelectorAll('button.ag-anchor, a.ag-anchor').length > 0,
                );
                if (!target) return false;
                const rows = target.querySelectorAll('.ag-row').length;
                const firstTitle = target.querySelector('button.ag-anchor, a.ag-anchor')?.innerText?.trim() || '';
                // Mudou o conteúdo OU tem rows (primeira carga)
                return firstTitle !== prev.firstTitle || (rows > 0 && prev.rowCount === 0);
            },
            preClickState,
            { timeout: 8000 },
        ).catch(() => pageLog.warning('   ⚠️ Grid não repopulou em 8s, prosseguindo assim mesmo'));

        // Pequeno settle adicional pra AG-Grid estabilizar
        await page.waitForTimeout(800);
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
        // Click + captura state de TODOS os panes antes (muitos podem existir)
        const context = await page.evaluate((anchorIndex) => {
            const grids = [...document.querySelectorAll('.ag-root-wrapper')];
            for (const g of grids) {
                if (g.offsetParent === null) continue;
                const anchors = [...g.querySelectorAll('button.ag-anchor, a.ag-anchor')];
                if (anchors.length > anchorIndex) {
                    const anchor = anchors[anchorIndex];

                    // Captura state de TODOS os panes existentes na DOM
                    const allPanes = [...document.querySelectorAll('.readingpane-details')];
                    const beforeStates = allPanes.map((p) => ({
                        text: (p.innerText || '').slice(0, 600),
                        visible: p.offsetParent !== null,
                    }));

                    anchor.click();
                    return { ok: true, beforeStates, paneCount: allPanes.length };
                }
            }
            return { ok: false };
        }, item.anchorIndex);

        if (!context.ok) {
            pageLog.warning(`   ⚠️ Anchor ${item.anchorIndex} não encontrado`);
            return null;
        }

        const dateHint = (item.date || '').match(/\d{2}\/\d{2}\/\d{4}\s+\d{2}:\d{2}:\d{2}/)?.[0] || null;

        // Espera QUALQUER pane atualizar: ou contém dateHint, ou mudou vs before
        await page.waitForFunction(
            ({ beforeStates, dateHint }) => {
                const allPanes = [...document.querySelectorAll('.readingpane-details')];
                for (let i = 0; i < allPanes.length; i++) {
                    const p = allPanes[i];
                    if (p.offsetParent === null) continue;
                    const current = (p.innerText || '').slice(0, 600);
                    if (!current || current.length < 50) continue;
                    // Se dateHint presente no pane — match forte
                    if (dateHint && current.includes(dateHint)) return true;
                    // Senão: pane mudou vs snapshot anterior
                    const before = beforeStates[i]?.text ?? null;
                    if (before === null || current !== before) return true;
                }
                return false;
            },
            { beforeStates: context.beforeStates, dateHint },
            { timeout: 15000 },
        ).catch(() => pageLog.warning('   ⚠️ Pane não atualizou em 15s (conteúdo pode estar stale)'));

        await page.waitForTimeout(600);

        // Pós-wait: descobre qual pane TEM o conteúdo certo (pra extração escopada)
        const paneIndex = await page.evaluate(
            ({ beforeStates, dateHint }) => {
                const allPanes = [...document.querySelectorAll('.readingpane-details')];
                // Prioridade 1: pane que contém dateHint
                if (dateHint) {
                    for (let i = 0; i < allPanes.length; i++) {
                        const p = allPanes[i];
                        if (p.offsetParent === null) continue;
                        if ((p.innerText || '').includes(dateHint)) return i;
                    }
                }
                // Prioridade 2: pane que MUDOU
                for (let i = 0; i < allPanes.length; i++) {
                    const p = allPanes[i];
                    if (p.offsetParent === null) continue;
                    const current = (p.innerText || '').slice(0, 600);
                    if (current.length < 50) continue;
                    if ((beforeStates[i]?.text ?? null) !== current) return i;
                }
                // Fallback: primeiro pane visível
                for (let i = 0; i < allPanes.length; i++) {
                    if (allPanes[i].offsetParent !== null) return i;
                }
                return -1;
            },
            { beforeStates: context.beforeStates, dateHint },
        );

        return { paneIndex };
    } catch (e) {
        pageLog.error(`   ❌ Erro abrindo artigo: ${e.message}`);
        return null;
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

            const content = await extractReadingPaneContent(page, opened.paneIndex);
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
