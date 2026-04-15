import { closePopups } from '../auth/login.js';
import { extractArticleImages, saveImagesToStore } from './images.js';
import { extractTables } from './tables.js';

export async function collectArticleContent(page, pageLog, item, options = {}) {
    const {
        returnUrl = null,
        collectImages = false,
        includeRawHtml = false,
        includeTables = true,
        noReturn = false,
    } = options;

    try {
        pageLog.info(`📖 Entrando: ${item.title.substring(0, 50)}...`);

        await closePopups(page);

        if (item.clickMethod === 'href' && item.href) {
            await page.goto(item.href, { waitUntil: 'domcontentloaded', timeout: 30000 });
        } else if (item.elementId) {
            // Worker precisa do contexto da listagem (Iron Ore topic) pra achar o elementId
            if (item.sourcePageUrl && !page.url().includes(new URL(item.sourcePageUrl).hash.split('?')[0].replace('#', ''))) {
                pageLog.info(`   Carregando contexto: ${item.sourcePageUrl}`);
                await page.goto(item.sourcePageUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
                await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
                await page.waitForSelector(`#${item.elementId}`, { timeout: 45000 })
                    .catch(() => pageLog.warning(`   ⚠️ ${item.elementId} não apareceu na source page em 45s`));
            }
            // Scroll pro item antes do click — items mais pra baixo no grid podem não estar em view
            await page.evaluate((id) => {
                document.getElementById(id)?.scrollIntoView({ block: 'center', behavior: 'instant' });
            }, item.elementId).catch(() => {});
            try {
                await page.click(`#${item.elementId}`, { timeout: 15000 });
            } catch (e) {
                await page.click(`#${item.elementId}`, { force: true, timeout: 15000 });
            }
        }

        // Espera conteúdo do artigo aparecer — usar selectors ESPECÍFICOS
        // (h1 global "News & Insights" do shell carrega em 1s e nos engana)
        await page.waitForSelector('.newsSection-headline, .newsSection-body', {
            timeout: 35000,
        }).catch(() => pageLog.warning('   ⚠️ Conteúdo do artigo não renderizou em 35s (provavelmente Rationale/item não-article)'));
        await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => { /* ok */ });

        const content = await page.evaluate(() => {
            const data = {
                title: '',
                author: '',
                publishDate: '',
                actualDate: '',
                paragraphs: [],
                fullText: '',
                metadata: {},
            };

            // A página de artigo é um feed contínuo: o artigo clicado fica no topo,
            // outros artigos aparecem empilhados abaixo. Precisamos escopar na 1ª peça.
            const mainHeadline = document.querySelector('.newsSection-headline');
            if (mainHeadline) {
                data.title = mainHeadline.textContent.trim();
            } else {
                // Fallback: primeiro h1 que NÃO seja "News & Insights"
                const h1s = [...document.querySelectorAll('h1')];
                const realH1 = h1s.find((h) => !/^news\s*&\s*insights$/i.test((h.innerText || '').trim()));
                if (realH1) data.title = realH1.textContent.trim();
            }

            // Container do artigo principal: a partir do parent do headline (evita auto-match via [class*="newsSection"])
            const articleRoot = mainHeadline?.parentElement?.closest(
                'article, .newsSection-article, .platts-newsSection-article, main',
            );

            // Helper: tenta primeiro scoped, depois global
            const scopedQuery = (selector) =>
                (articleRoot && articleRoot.querySelector(selector)) ||
                document.querySelector(selector);

            // Autor
            const publisherBody = scopedQuery('.newsSection-publisher-content .publisher-body') ||
                scopedQuery('.publisher-body');
            if (publisherBody) {
                data.author = publisherBody.innerText.trim().replace(/\|.*$/, '').trim();
            } else {
                const fallbackAuthor = scopedQuery('.author-data-email-link span') || scopedQuery('[class*="author"] span');
                if (fallbackAuthor) data.author = fallbackAuthor.textContent.trim();
            }

            // Body — primeiro escopado no articleRoot, depois fallback global
            const bodyEl = scopedQuery('.newsSection-body') ||
                scopedQuery('.platts-newsSection-article') ||
                document.querySelector('article');
            if (bodyEl) {
                bodyEl.querySelectorAll('p').forEach((p) => {
                    const text = p.textContent.trim();
                    if (text.length > 20) data.paragraphs.push(text);
                });
                data.fullText = data.paragraphs.join('\n\n');
            }

            // Data — procura regex UTC no texto do scoped body (ou articleRoot),
            // não no document.body global (que tem datas de outros artigos empilhados).
            const dateScopeEl = articleRoot || bodyEl || document.body;
            const scopedText = dateScopeEl.innerText || '';
            const dateMatch = scopedText.match(/\d{2}\/\d{2}\/\d{4}\s+\d{2}:\d{2}:\d{2}\s+UTC/);
            if (dateMatch) {
                data.publishDate = dateMatch[0];
                data.actualDate = dateMatch[0];
            } else {
                const anyDate = scopedText.match(/\d{2}\/\d{2}\/\d{4}/);
                if (anyDate) {
                    data.publishDate = anyDate[0];
                    data.actualDate = anyDate[0];
                }
            }

            // Highlights
            const highlightsEl = scopedQuery('.newsSection-highlights');
            if (highlightsEl) {
                const bullets = highlightsEl.querySelectorAll('li');
                data.metadata.highlights = Array.from(bullets).map((li) => li.textContent.trim()).filter(Boolean);
            }

            data.metadata.wordCount = data.fullText.split(/\s+/).filter((w) => w).length;
            data.metadata.paragraphCount = data.paragraphs.length;

            const prices = data.fullText.match(/\$[\d,]+\.?\d*\s*(billion|million)?/gi);
            if (prices) data.metadata.prices = [...new Set(prices)];

            const percents = data.fullText.match(/[\d]+\.?\d*%/g);
            if (percents) data.metadata.percentages = [...new Set(percents)];

            const commodityPrices = data.fullText.match(/\$[\d,.]+\/(?:mt|ton|tonne|dmt|kg|lb)/gi);
            if (commodityPrices) data.metadata.commodityPrices = [...new Set(commodityPrices)];

            const companies = [
                'US Steel', 'Nippon Steel', 'Vale', 'Rio Tinto', 'BHP',
                'ArcelorMittal', 'Fortescue', 'Anglo American', 'POSCO', 'Baowu',
                'Cleveland-Cliffs', 'Nucor', 'Tata Steel', 'JFE', 'Shougang',
            ];
            data.metadata.companies = companies.filter((c) =>
                data.fullText.toLowerCase().includes(c.toLowerCase()),
            );

            return data;
        });

        if (collectImages) {
            const articleId = item.href?.match(/articleID=([^&]+)/)?.[1] ||
                item.href?.match(/newsArticle\?articleID=([^&]+)/)?.[1] ||
                `article_${Date.now()}`;

            const imagesData = await extractArticleImages(page, pageLog);

            let savedImages = [];
            if (imagesData.length > 0) {
                savedImages = await saveImagesToStore(imagesData, articleId, pageLog);
            }

            content.images = {
                articleId,
                total: imagesData.length,
                thumbnail: savedImages.find((i) => i.type === 'thumbnail') || null,
                charts: savedImages.filter((i) => i.type === 'chart'),
                savedFiles: savedImages.map((i) => i.filename),
            };

            content.metadata.imageCount = imagesData.length;
            content.metadata.chartCount = imagesData.filter((i) => i.type === 'chart').length;
            content.metadata.thumbnailSaved = savedImages.some((i) => i.type === 'thumbnail');
        }

        if (includeRawHtml) {
            content.rawHtml = await page.locator(
                '.newsSection-body, .platts-newsSection-article, article',
            ).first().innerHTML().catch(() => null);
        }

        if (includeTables) {
            content.tables = await extractTables(page).catch((e) => {
                pageLog.warning(`   ⚠️ Erro extraindo tabelas: ${e.message}`);
                return [];
            });
            if (content.tables?.length) {
                pageLog.info(`   📊 ${content.tables.length} tabelas extraídas`);
            }
        }

        // gridDateTime: data capturada no listing (antes do click), preserva mesmo se actualDate sobrescrever item.date
        content.gridDateTime = item.date;

        if (content.actualDate) {
            item.date = content.actualDate;
            pageLog.info(`   📅 Data do artigo: ${content.actualDate}`);
        }

        const imageInfo = collectImages ? `, ${content.metadata.imageCount || 0} imagens` : '';
        pageLog.info(`   ✅ ${content.metadata.wordCount} palavras${imageInfo}`);

        if (!noReturn) {
            if (returnUrl) {
                await page.goto(returnUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
                await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => { /* ok */ });
            } else {
                await page.goBack().catch(() => { /* ignora */ });
                await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => { /* ok */ });
            }
            await closePopups(page);
        }

        return { ...item, ...content, extractedAt: new Date().toISOString() };
    } catch (error) {
        pageLog.error(`Erro artigo: ${error.message}`);
        if (!noReturn) {
            try {
                if (returnUrl) await page.goto(returnUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
                else await page.goBack();
            } catch (e) { /* ignora */ }
        }
        return null;
    }
}
