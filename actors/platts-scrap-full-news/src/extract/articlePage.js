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
            try {
                await page.click(`#${item.elementId}`, { timeout: 5000 });
            } catch (e) {
                await page.click(`#${item.elementId}`, { force: true, timeout: 5000 });
            }
        }

        // Espera cabeçalho ou corpo do artigo aparecer (em vez de wait fixo 8s)
        await page.waitForSelector('h1, .newsSection-headline, .newsSection-body, article', {
            timeout: 20000,
        }).catch(() => pageLog.warning('   ⚠️ Cabeçalho/corpo não apareceu em 20s'));
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

            const h1 = document.querySelector('h1, .newsSection-headline');
            if (h1) data.title = h1.textContent.trim();

            const authorLink = document.querySelector('.author-data-email-link span');
            if (authorLink) {
                data.author = authorLink.textContent.trim();
            } else {
                const authorEl = document.querySelector('[class*="author"], .publisher-header span');
                if (authorEl) data.author = authorEl.textContent.trim().replace('Author', '').replace('|', '').trim();
            }

            const pageText = document.body.innerText;
            const dateMatch = pageText.match(/\d{2}\/\d{2}\/\d{4}\s+\d{2}:\d{2}:\d{2}\s+UTC/);
            if (dateMatch) {
                data.publishDate = dateMatch[0];
                data.actualDate = dateMatch[0];
            }

            const highlightsEl = document.querySelector('.newsSection-highlights');
            if (highlightsEl) {
                const bullets = highlightsEl.querySelectorAll('li');
                data.metadata.highlights = Array.from(bullets).map((li) => li.textContent.trim());
            }

            const bodyEl = document.querySelector('.newsSection-body, .platts-newsSection-article, article');
            if (bodyEl) {
                bodyEl.querySelectorAll('p').forEach((p) => {
                    const text = p.textContent.trim();
                    if (text.length > 20) data.paragraphs.push(text);
                });
                data.fullText = data.paragraphs.join('\n\n');
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
