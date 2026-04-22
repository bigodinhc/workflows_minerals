/**
 * Platts Iron Ore News Scraper (refactored — merged full-news + news-only)
 *
 * Fontes:
 * - allInsights Ferrous Metals: FLASH banner + Top News (slider) + Latest list
 * - Iron Ore topic: News & Insights
 * - Raw Materials Workspace: Commentary/Rationale tabs via reading pane (descoberta dinâmica)
 *
 * Estratégia:
 * - Coleta sequencial de listagens na page principal
 * - FLASH captura direto (sem extração de artigo, é texto do banner)
 * - Artigos das fontes padrão A (allInsights + ironOreTopic) extraídos em paralelo
 * - RMW (padrão B) executado sequencial na page principal
 */

import { PlaywrightCrawler, log } from 'crawlee';
import { Actor } from 'apify';

import { loginPlatts } from './auth/login.js';
import {
    navigateToFerrousMetals,
    collectFlashBanner,
    collectTopNewsList,
    collectLatestList,
} from './sources/allInsights.js';
import { navigateToIronOre, collectNewsList } from './sources/ironOreTopic.js';
import { collectRMW } from './sources/rmw.js';
import { collectArticleContent } from './extract/articlePage.js';
import { isDateWithinFilter } from './util/dates.js';
import { saveDebugArtifacts } from './util/debug.js';
import { createLimiter } from './util/semaphore.js';
import { EventBus } from './lib/eventBus.js';

await Actor.init();

const input = await Actor.getInput() ?? {};

log.info('🔍 Input:');
log.info(JSON.stringify({ ...input, password: input.password ? '***' : null }, null, 2));

const {
    username,
    password,
    sources = ['allInsights', 'ironOreTopic', 'rmw'],
    maxArticles = 10,
    maxTopNewsToCheck = 5,
    latestMaxItems = 30,
    scrollForMoreLatest = false,
    rmwTabFilter = '',
    maxArticlesPerRmwTab = 10,
    concurrency = 3,
    collectTopNews = true,
    includeLatest = true,
    includeFlash = true,
    collectImages = false,
    includeRawHtml = false,
    includeTables = true,
    dedupArticles = true,
    dateFilter = 'today',
    daysToCollect = 1,
    targetDate = null,
    debugArtifacts = false,
} = input;

const bus = new EventBus({
    workflow: 'platts_scrap_full_news',
    traceId: input.trace_id,
    parentRunId: input.parent_run_id,
});

await bus.emit('cron_started', {
    detail: { apify_run_id: process.env.ACTOR_RUN_ID ?? null },
});

const articleOptions = { collectImages, includeRawHtml, includeTables };

log.info('=====================================');
log.info('Platts Iron Ore News Scraper (merged — Etapa 4)');
log.info('=====================================');
log.info(`Sources: ${sources.join(', ')}`);
log.info(`Data alvo: ${targetDate || 'hoje'} | Filtro: ${dateFilter}`);
log.info(`Max artigos: ${maxArticles} | Concurrency: ${concurrency}`);
log.info(`Top News: ${collectTopNews} | Latest: ${includeLatest} (max ${latestMaxItems}) | Flash: ${includeFlash}`);
log.info(`RMW tabs filter: "${rmwTabFilter}" | per-tab max: ${maxArticlesPerRmwTab}`);
log.info(`Imagens: ${collectImages} | rawHtml: ${includeRawHtml} | tables: ${includeTables}`);
log.info(`Dedup: ${dedupArticles} | Debug: ${debugArtifacts}`);
log.info('=====================================');

if (!username || !password) {
    await Actor.pushData({ type: 'error', message: 'Credenciais necessárias' });
    await bus.emit('cron_crashed', {
        label: 'ConfigError: username or password not provided',
        detail: { exc_type: 'ConfigError', exc_str: 'username or password not provided' },
        level: 'error',
    });
    await Actor.exit();
}

const proxyConfiguration = await Actor.createProxyConfiguration(input.proxyConfiguration);
if (proxyConfiguration) {
    log.info(`🌐 Proxy ativo: ${JSON.stringify(input.proxyConfiguration)}`);
}

function dedup(items) {
    const seen = new Set();
    const out = [];
    for (const it of items) {
        const key = (it.href || it.title || '').toLowerCase().trim();
        if (!key || seen.has(key)) continue;
        seen.add(key);
        out.push(it);
    }
    return out;
}

const crawler = new PlaywrightCrawler({
    launchContext: {
        launchOptions: { headless: true, args: ['--no-sandbox'] },
        useChrome: true,
    },
    proxyConfiguration,
    useSessionPool: true,
    persistCookiesPerSession: true,
    maxRequestRetries: 3,
    requestHandlerTimeoutSecs: 1800,
    navigationTimeoutSecs: 60,

    async failedRequestHandler({ request, error, page }) {
        log.error(`❌ Request falhou após retries: ${request.url} — ${error?.message}`);
        if (page) {
            await saveDebugArtifacts(page, `failed-${request.id}`, {
                error: error?.message,
                stack: error?.stack?.substring(0, 2000),
                retryCount: request.retryCount,
            });
        }
        await Actor.pushData({
            type: 'error',
            url: request.url,
            error: error?.message || 'unknown',
            retryCount: request.retryCount,
            timestamp: new Date().toISOString(),
        });
    },

    async requestHandler({ page, request, session, log: pageLog }) {
        page.setDefaultTimeout(30000);

        // ========== LOGIN ==========
        pageLog.info('🔐 Login...');
        const login = await loginPlatts(page, username, password, pageLog);
        if (!login.ok) {
            pageLog.error(`Login falhou: ${login.reason} — ${login.error}`);
            if (debugArtifacts || login.reason === 'auth-rejected') {
                await saveDebugArtifacts(page, `login-${login.reason}`, { error: login.error });
            }
            if (login.reason === 'auth-rejected') {
                session?.markBad();
                request.noRetry = true;
                throw new Error(`Auth rejected: ${login.error}`);
            }
            throw new Error(`Login falhou (${login.reason}): ${login.error}`);
        }
        pageLog.info('✅ Login OK!');
        session?.markGood();

        // ========== COLETA DE LISTAGENS ==========
        const articleItems = []; // itens para extração paralela (padrão A)
        let flashItems = [];

        if (sources.includes('allInsights')) {
            pageLog.info('\n========== ALL INSIGHTS (Ferrous Metals) ==========');
            if (await navigateToFerrousMetals(page, pageLog)) {
                if (includeFlash) {
                    flashItems = await collectFlashBanner(page, pageLog);
                }

                if (collectTopNews) {
                    const topNewsList = await collectTopNewsList(page, pageLog, maxTopNewsToCheck);
                    articleItems.push(...topNewsList);
                }

                if (includeLatest) {
                    const latestList = await collectLatestList(page, pageLog, latestMaxItems, scrollForMoreLatest);
                    articleItems.push(...latestList);
                }
            } else {
                pageLog.warning('⚠️ allInsights indisponível');
            }
        }

        if (sources.includes('ironOreTopic')) {
            pageLog.info('\n========== IRON ORE TOPIC ==========');
            if (await navigateToIronOre(page, pageLog)) {
                const news = await collectNewsList(
                    page, pageLog, maxArticles, dateFilter, daysToCollect, targetDate,
                );
                articleItems.push(...news);
            } else {
                pageLog.warning('⚠️ Iron Ore topic indisponível');
            }
        }

        // ========== RMW (sequencial, padrão B) ==========
        let rmwResults = [];
        if (sources.includes('rmw')) {
            pageLog.info('\n========== RAW MATERIALS WORKSPACE ==========');
            rmwResults = await collectRMW(page, pageLog, {
                maxArticlesPerTab: maxArticlesPerRmwTab,
                dateFilter,
                daysToCollect,
                targetDate,
                tabFilter: rmwTabFilter,
            });
        }

        // Dedup cross-source (só padrão A)
        const uniqueItems = dedupArticles ? dedup(articleItems) : articleItems;
        pageLog.info(`\n📦 Padrão A: ${articleItems.length} itens, ${uniqueItems.length} após dedup`);
        pageLog.info(`📦 Padrão B (RMW): ${rmwResults.reduce((s, t) => s + t.articles.length, 0)} artigos em ${rmwResults.length} tabs`);
        pageLog.info(`📦 FLASH: ${flashItems.length} item(ns)`);

        // ========== EXTRAÇÃO PARALELA DE ARTIGOS (padrão A) ==========
        let articles = [];
        let failedArticles = 0;

        if (uniqueItems.length > 0) {
            pageLog.info(`\n========== EXTRAÇÃO PARALELA (concurrency=${concurrency}) ==========`);

            const context = page.context();
            const limit = createLimiter(concurrency);
            const quota = { filled: 0 };

            const results = await Promise.all(uniqueItems.map((item, idx) => limit(async () => {
                if (quota.filled >= maxArticles) return null;

                const workerPage = await context.newPage();
                try {
                    pageLog.info(`📖 [${idx + 1}/${uniqueItems.length}] ${item.source}: "${(item.title || '').substring(0, 60)}..."`);

                    const content = await collectArticleContent(
                        workerPage, pageLog, item, { ...articleOptions, noReturn: true },
                    );

                    if (!content?.fullText) {
                        failedArticles++;
                        if (debugArtifacts) {
                            await saveDebugArtifacts(workerPage, `worker-empty-${item.source}-${idx}`, { item });
                        }
                        return null;
                    }

                    // Top News: filtro de data após extração (listing não tem data)
                    if ((item.source.includes('Top News') || item.source === 'Latest') &&
                        dateFilter !== 'all' && !item.date &&
                        !isDateWithinFilter(content.actualDate, dateFilter, daysToCollect, targetDate)) {
                        pageLog.info(`   ⏭️ Fora do filtro: ${content.actualDate}`);
                        return null;
                    }

                    if (quota.filled >= maxArticles) return null;
                    quota.filled++;
                    pageLog.info(`   ✅ [${quota.filled}/${maxArticles}] ${content.metadata?.wordCount || 0} palavras`);
                    return content;
                } catch (e) {
                    failedArticles++;
                    pageLog.error(`   ❌ Erro: ${e.message}`);
                    if (debugArtifacts) {
                        await saveDebugArtifacts(workerPage, `worker-error-${item.source}-${idx}`, {
                            error: e.message, item,
                        });
                    }
                    return null;
                } finally {
                    await workerPage.close().catch(() => {});
                }
            })));

            articles = results.filter(Boolean);
        }

        // ========== RESULTADO ==========
        const rmwArticles = rmwResults.flatMap((t) => t.articles);
        const allArticlesFlat = [...articles, ...rmwArticles];

        if (allArticlesFlat.length === 0 && flashItems.length === 0) {
            pageLog.warning(`Nenhum item encontrado para ${targetDate || 'hoje'}`);
            await Actor.pushData({
                type: 'no_data',
                message: 'Nenhum item encontrado',
                dateFilter, targetDate,
                summary: { failedArticles, totalAttempted: uniqueItems.length },
                timestamp: new Date().toISOString(),
            });
            return;
        }

        const topNews = articles.filter((a) => a.source?.includes('Top News'));
        const latest = articles.filter((a) => a.source === 'Latest');
        const newsInsights = articles.filter((a) => a.source === 'News & Insights');

        const totalImages = allArticlesFlat.reduce((sum, a) => sum + (a.images?.total || 0), 0);
        const totalCharts = allArticlesFlat.reduce((sum, a) => sum + (a.images?.charts?.length || 0), 0);
        const allSavedFiles = allArticlesFlat.flatMap((a) => a.images?.savedFiles || []);

        const result = {
            type: 'success',
            dateFilter, targetDate, collectImages, sources,
            summary: {
                totalArticles: allArticlesFlat.length,
                topNews: topNews.length,
                latest: latest.length,
                newsInsights: newsInsights.length,
                flash: flashItems.length,
                rmwArticles: rmwArticles.length,
                rmwTabs: rmwResults.map((t) => ({ tabName: t.tabName, articleCount: t.articles.length })),
                failedArticles,
                totalAttempted: uniqueItems.length,
                totalWords: allArticlesFlat.reduce((s, a) => s + (a.metadata?.wordCount || 0), 0),
                totalImages, totalCharts, savedImageFiles: allSavedFiles,
                companiesMentioned: [...new Set(allArticlesFlat.flatMap((a) => a.metadata?.companies || []))],
                pricesFound: [...new Set(allArticlesFlat.flatMap((a) => a.metadata?.prices || []))],
                iodexPrices: [...new Set(allArticlesFlat.map((a) => a.metadata?.iodexPrice).filter(Boolean))],
            },
            flash: flashItems,
            topNews,
            latest,
            newsInsights,
            marketCommentary: [],  // deprecated: iron-ore Market Commentary agora vem da RMW
            rmw: rmwResults,
            allArticles: allArticlesFlat,
            extractedAt: new Date().toISOString(),
        };

        await Actor.pushData(result);

        log.info('\n=====================================');
        log.info('📊 RESUMO');
        log.info(`✅ Total: ${allArticlesFlat.length} artigos + ${flashItems.length} flash`);
        log.info(`   ⭐ Top News: ${topNews.length}`);
        log.info(`   📰 Latest: ${latest.length}`);
        log.info(`   📃 News & Insights: ${newsInsights.length}`);
        log.info(`   💬 RMW: ${rmwArticles.length} em ${rmwResults.length} tabs`);
        log.info(`   ❌ Falhas: ${failedArticles}`);
        if (collectImages) {
            log.info(`   🖼️ Imagens: ${totalImages} (${totalCharts} gráficos)`);
        }
        log.info('=====================================');
    },
});

try {
    await crawler.run(['about:blank']);
    log.info('🏁 Fim!');
    await bus.emit('cron_finished', {
        detail: { ok: true },
    });
    await Actor.exit();
} catch (err) {
    const errName = err?.name ?? 'UnknownError';
    const errMsg = err?.message ?? String(err ?? '');
    await bus.emit('cron_crashed', {
        label: `${errName}: ${errMsg.slice(0, 100)}`,
        detail: {
            exc_type: errName,
            exc_str: String(err ?? '').slice(0, 500),
        },
        level: 'error',
    });
    await Actor.fail(errMsg || String(err ?? 'unknown error'));
}
