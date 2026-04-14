/**
 * Platts Iron Ore Scraper v5.9 - COM SUPORTE A IMAGENS
 * 
 * NOVIDADES desta versão:
 * - Extração de thumbnail principal do artigo
 * - Extração de gráficos/charts do corpo do artigo
 * - Salvamento de imagens no Key-Value Store da Apify
 * - Parâmetro collectImages para ativar/desativar coleta de imagens
 * - Metadados de imagens incluídos no output
 * 
 * Funcionalidades mantidas:
 * - Coleta Top News da página Ferrous Metals
 * - Coleta News & Insights da página Iron Ore
 * - Coleta Market Commentary da página Iron Ore
 * - Detecção automática de formato de data
 * - Suporte a targetDate para timezone Brasil
 */

import { PlaywrightCrawler, log } from 'crawlee';
import { Actor } from 'apify';

await Actor.init();

// ========== VARIÁVEL GLOBAL ==========
let globalTargetDate = null;

/**
 * Formatar data de forma consistente
 */
function formatDateBR(date) {
    if (!date || isNaN(date.getTime())) return 'DATA INVÁLIDA';
    const day = String(date.getUTCDate()).padStart(2, '0');
    const month = String(date.getUTCMonth() + 1).padStart(2, '0');
    const year = date.getUTCFullYear();
    return `${day}/${month}/${year}`;
}

/**
 * Detectar e fechar popups
 */
async function closePopups(page, pageLog) {
    try {
        await page.evaluate(() => {
            const selectors = ['.QSIWebResponsive', '.QSIPopOver', '[id*="QSI"]', '.modal-backdrop'];
            selectors.forEach(sel => {
                document.querySelectorAll(sel).forEach(el => {
                    el.style.display = 'none';
                    el.remove();
                });
            });
        });
    } catch (e) {}
}

/**
 * Converte tempo relativo em data absoluta
 */
function parseRelativeTime(timeString) {
    if (!timeString) return null;
    
    const now = new Date();
    const lowerTime = timeString.toLowerCase().trim();
    
    const patterns = [
        { regex: /há\s+(\d+)\s+minuto/i, unit: 'minutes' },
        { regex: /há\s+(\d+)\s+hora/i, unit: 'hours' },
        { regex: /há\s+(\d+)\s+dia/i, unit: 'days' },
        { regex: /(\d+)\s+minute[s]?\s+ago/i, unit: 'minutes' },
        { regex: /(\d+)\s+hour[s]?\s+ago/i, unit: 'hours' },
        { regex: /(\d+)\s+day[s]?\s+ago/i, unit: 'days' }
    ];
    
    for (const pattern of patterns) {
        const match = lowerTime.match(pattern.regex);
        if (match) {
            const value = parseInt(match[1]);
            const resultDate = new Date(now);
            
            switch (pattern.unit) {
                case 'minutes': resultDate.setMinutes(resultDate.getMinutes() - value); break;
                case 'hours': resultDate.setHours(resultDate.getHours() - value); break;
                case 'days': resultDate.setDate(resultDate.getDate() - value); break;
            }
            
            return resultDate;
        }
    }
    
    return null;
}

/**
 * Parse de data com DETECÇÃO AUTOMÁTICA DE FORMATO
 * Suporta DD/MM/YYYY e MM/DD/YYYY
 */
function parsePlattsDate(dateString) {
    if (!dateString) return null;
    
    // Tentar tempo relativo primeiro
    const relativeDate = parseRelativeTime(dateString);
    if (relativeDate) return relativeDate;
    
    try {
        const cleanDate = dateString.replace('UTC', '').trim();
        
        // Formato: XX/XX/YYYY HH:MM:SS
        const parts = cleanDate.match(/(\d{2})\/(\d{2})\/(\d{4})\s+(\d{2}):(\d{2}):(\d{2})/);
        if (parts) {
            const first = parseInt(parts[1]);
            const second = parseInt(parts[2]);
            const year = parseInt(parts[3]);
            const hour = parseInt(parts[4]);
            const minute = parseInt(parts[5]);
            const second_time = parseInt(parts[6]);
            
            let day, month;
            
            // DETECÇÃO AUTOMÁTICA DE FORMATO:
            // Se o primeiro número > 12, só pode ser dia (DD/MM/YYYY)
            // Se o segundo número > 12, só pode ser dia (MM/DD/YYYY)
            // Se ambos <= 12, assumir MM/DD/YYYY (formato americano do servidor)
            
            if (first > 12) {
                day = first;
                month = second - 1;
                log.info(`      📆 Formato detectado: DD/MM/YYYY (primeiro=${first} > 12)`);
            } else if (second > 12) {
                month = first - 1;
                day = second;
                log.info(`      📆 Formato detectado: MM/DD/YYYY (segundo=${second} > 12)`);
            } else {
                month = first - 1;
                day = second;
                log.info(`      📆 Formato assumido: MM/DD/YYYY (ambos <= 12)`);
            }
            
            const date = new Date(Date.UTC(year, month, day, hour, minute, second_time));
            return date;
        }
        
        return null;
    } catch (error) {
        return null;
    }
}

/**
 * Verifica se a data está dentro do filtro
 */
function isDateWithinFilter(dateString, filterType, daysBack = 1) {
    log.info(`      🔍 Verificando data: "${dateString}"`);
    
    const articleDate = parsePlattsDate(dateString);
    
    if (!articleDate) {
        if (dateString && (dateString.includes('há') || dateString.includes('ago'))) {
            log.info(`      ✅ Tempo relativo - assumindo hoje`);
            return true;
        }
        log.warning(`      ⚠️ Parse falhou`);
        return filterType === 'all';
    }
    
    log.info(`      📅 Data parseada: ${formatDateBR(articleDate)}`);
    
    // Data de referência
    let targetDay;
    if (globalTargetDate && (filterType === 'specificDate' || filterType === 'today')) {
        const parts = globalTargetDate.match(/(\d{2})\/(\d{2})\/(\d{4})/);
        if (parts) {
            targetDay = new Date(Date.UTC(parseInt(parts[3]), parseInt(parts[2]) - 1, parseInt(parts[1])));
        }
    }
    
    if (!targetDay) {
        const now = new Date();
        targetDay = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
    }
    
    log.info(`      🎯 Data alvo: ${formatDateBR(targetDay)}`);
    
    const articleDay = new Date(Date.UTC(
        articleDate.getUTCFullYear(), 
        articleDate.getUTCMonth(), 
        articleDate.getUTCDate()
    ));
    
    switch (filterType) {
        case 'today':
        case 'specificDate':
            const isMatch = articleDay.getTime() === targetDay.getTime();
            log.info(`      ${isMatch ? '✅ MATCH!' : '❌ Não é a data alvo'}`);
            return isMatch;
            
        case 'lastXDays':
            const cutoffDate = new Date(targetDay);
            cutoffDate.setUTCDate(cutoffDate.getUTCDate() - daysBack);
            return articleDay >= cutoffDate;
            
        case 'all':
            return true;
            
        default:
            return true;
    }
}

// ========== FUNÇÕES DE IMAGEM (NOVAS) ==========

/**
 * Extrair imagens do artigo
 * Retorna array de objetos com dados das imagens
 */
async function extractArticleImages(page, pageLog) {
    try {
        pageLog.info('   🖼️ Extraindo imagens do artigo...');
        
        const imagesData = await page.evaluate(() => {
            const images = [];
            const articleEl = document.querySelector('.platts-newsSection-article') || 
                             document.querySelector('article') ||
                             document.querySelector('.newsSection-body');
            
            if (!articleEl) return images;
            
            // 1. Thumbnail principal
            const thumbnailImg = articleEl.querySelector('.platts-news-article-thumbnail img') ||
                                document.querySelector('.platts-news-article-thumbnail img');
            
            if (thumbnailImg && thumbnailImg.src && thumbnailImg.src.length > 100) {
                // Detectar tipo MIME do base64
                let mimeType = 'image/jpeg';
                const mimeMatch = thumbnailImg.src.match(/data:([^;]+);/);
                if (mimeMatch) {
                    mimeType = mimeMatch[1];
                }
                
                images.push({
                    type: 'thumbnail',
                    index: 0,
                    src: thumbnailImg.src,
                    width: thumbnailImg.naturalWidth || thumbnailImg.width || 0,
                    height: thumbnailImg.naturalHeight || thumbnailImg.height || 0,
                    alt: thumbnailImg.alt || '',
                    caption: '',
                    isBase64: thumbnailImg.src.startsWith('data:'),
                    mimeType: mimeType,
                    sizeKB: Math.round(thumbnailImg.src.length * 0.75 / 1024) // Aproximação do tamanho base64
                });
            }
            
            // 2. Gráficos no corpo do artigo
            const bodyEl = articleEl.querySelector('.newsSection-body') || articleEl;
            const allImgs = bodyEl.querySelectorAll('img');
            
            let chartIndex = 0;
            allImgs.forEach((img) => {
                // Pular thumbnail (já coletada)
                if (img.closest('.platts-news-article-thumbnail') || 
                    img.closest('.platts-inarticle-thumbnail-container') ||
                    img.alt === 'Thumbnail Image') {
                    return;
                }
                
                const src = img.src || '';
                if (!src || src.length < 500) return; // Pular imagens muito pequenas (ícones)
                
                // Detectar tipo MIME
                let mimeType = 'image/png';
                const mimeMatch = src.match(/data:([^;]+);/);
                if (mimeMatch) {
                    mimeType = mimeMatch[1];
                }
                
                // Procurar legenda/contexto - verificar elementos próximos
                let caption = '';
                const parent = img.parentElement;
                
                if (parent) {
                    // Verificar texto no próximo parágrafo
                    let nextEl = parent.nextElementSibling;
                    while (nextEl && !caption) {
                        const text = nextEl.textContent?.trim() || '';
                        // Pegar texto que parece ser legenda ou contexto
                        if (text.length > 20 && text.length < 500 && !text.startsWith('data:')) {
                            caption = text.substring(0, 200);
                            break;
                        }
                        nextEl = nextEl.nextElementSibling;
                    }
                }
                
                chartIndex++;
                images.push({
                    type: 'chart',
                    index: chartIndex,
                    src: src,
                    width: img.naturalWidth || img.width || 0,
                    height: img.naturalHeight || img.height || 0,
                    alt: img.alt || '',
                    caption: caption,
                    isBase64: src.startsWith('data:'),
                    mimeType: mimeType,
                    sizeKB: Math.round(src.length * 0.75 / 1024)
                });
            });
            
            return images;
        });
        
        const thumbnailCount = imagesData.filter(i => i.type === 'thumbnail').length;
        const chartCount = imagesData.filter(i => i.type === 'chart').length;
        
        pageLog.info(`      📷 Thumbnail: ${thumbnailCount}`);
        pageLog.info(`      📈 Gráficos: ${chartCount}`);
        
        return imagesData;
        
    } catch (error) {
        pageLog.error(`   ❌ Erro extraindo imagens: ${error.message}`);
        return [];
    }
}

/**
 * Salvar imagens no Key-Value Store da Apify
 * Converte base64 para Buffer e salva como arquivo
 */
async function saveImagesToStore(images, articleId, pageLog) {
    const savedImages = [];
    
    for (const img of images) {
        try {
            if (!img.src || !img.isBase64) continue;
            
            // Extrair dados base64
            const base64Match = img.src.match(/data:([^;]+);base64,(.+)/);
            if (!base64Match) {
                pageLog.warning(`      ⚠️ Formato base64 inválido para imagem ${img.index}`);
                continue;
            }
            
            const mimeType = base64Match[1];
            const base64Data = base64Match[2];
            
            // Determinar extensão baseada no MIME type
            let extension = 'png';
            if (mimeType.includes('jpeg') || mimeType.includes('jpg')) extension = 'jpg';
            else if (mimeType.includes('svg')) extension = 'svg';
            else if (mimeType.includes('png')) extension = 'png';
            else if (mimeType.includes('gif')) extension = 'gif';
            else if (mimeType.includes('webp')) extension = 'webp';
            
            // Criar nome do arquivo seguro
            const safeArticleId = articleId.replace(/[^a-zA-Z0-9-_]/g, '_').substring(0, 50);
            const filename = `${safeArticleId}_${img.type}_${img.index}.${extension}`;
            
            // Converter base64 para Buffer
            const buffer = Buffer.from(base64Data, 'base64');
            
            // Salvar no Key-Value Store
            await Actor.setValue(filename, buffer, { contentType: mimeType });
            
            savedImages.push({
                filename: filename,
                type: img.type,
                index: img.index,
                width: img.width,
                height: img.height,
                caption: img.caption,
                mimeType: mimeType,
                sizeKB: Math.round(buffer.length / 1024),
                storeKey: filename
            });
            
            pageLog.info(`      ✅ Salvo: ${filename} (${Math.round(buffer.length / 1024)}KB)`);
            
        } catch (error) {
            pageLog.error(`      ❌ Erro salvando imagem ${img.type}_${img.index}: ${error.message}`);
        }
    }
    
    return savedImages;
}

// ========== FIM FUNÇÕES DE IMAGEM ==========

/**
 * Login no Platts
 */
async function loginPlatts(page, username, password, pageLog) {
    try {
        pageLog.info('Navegando para página inicial...');
        await page.goto('https://www.spglobal.com/commodityinsights/en', {
            waitUntil: 'domcontentloaded', timeout: 30000
        });
        await page.waitForTimeout(3000);
        await closePopups(page, pageLog);
        
        pageLog.info('Navegando para login...');
        await page.goto('https://www.spglobal.com/bin/commodityinsights/login', {
            waitUntil: 'networkidle', timeout: 30000
        });
        await page.waitForTimeout(10000);
        
        if (page.url().includes('commodity-insights')) {
            await page.goto('https://www.spglobal.com/bin/commodityinsights/login', {
                waitUntil: 'networkidle', timeout: 30000
            });
            await page.waitForTimeout(5000);
        }
        
        pageLog.info('Preenchendo username...');
        await page.waitForSelector('input[name="identifier"]', { timeout: 20000, state: 'visible' });
        await page.click('input[name="identifier"]');
        await page.fill('input[name="identifier"]', '');
        await page.type('input[name="identifier"]', username, { delay: 100 });
        
        pageLog.info('Clicando Next...');
        await page.waitForTimeout(1000);
        const nextBtn = await page.$('input[type="submit"][value="Next"]') || await page.$('input[type="submit"]');
        if (nextBtn) await nextBtn.click();
        else await page.keyboard.press('Enter');
        
        await page.waitForTimeout(5000);
        
        if (await page.$('div[data-se="okta_password"]')) {
            pageLog.info('Selecionando método senha...');
            try {
                await page.click('div[data-se="okta_password"] a.select-factor', { timeout: 3000 });
            } catch (e) {
                await page.evaluate(() => {
                    const div = document.querySelector('div[data-se="okta_password"]');
                    if (div) div.querySelector('a.select-factor')?.click();
                });
            }
            await page.waitForTimeout(5000);
        }
        
        pageLog.info('Preenchendo senha...');
        await page.waitForSelector('input[type="password"], input[name="credentials.passcode"]', { timeout: 15000 });
        const passField = await page.$('input[name="credentials.passcode"]') || await page.$('input[type="password"]');
        await passField.click();
        await passField.fill(password);
        
        await page.waitForTimeout(1000);
        const verifyBtn = await page.$('input[type="submit"][value="Verify"]') || await page.$('input[type="submit"]');
        if (verifyBtn) await verifyBtn.click();
        else await page.keyboard.press('Enter');
        
        pageLog.info('Aguardando autenticação...');
        await page.waitForTimeout(15000);
        
        pageLog.info(`Login OK. URL: ${page.url()}`);
        return true;
        
    } catch (error) {
        pageLog.error(`Erro login: ${error.message}`);
        return false;
    }
}

/**
 * Navegar para Ferrous Metals
 */
async function navigateToFerrousMetals(page, pageLog) {
    try {
        pageLog.info('🧭 Navegando para Ferrous Metals...');
        
        await page.goto('https://plattsconnect.spglobal.com/#platts/allInsights?keySector=Ferrous%20Metals', {
            waitUntil: 'domcontentloaded', timeout: 30000
        });
        
        await page.waitForTimeout(5000);
        await closePopups(page, pageLog);
        
        try {
            await page.waitForSelector('#platts-topNews-slider', { timeout: 15000, state: 'visible' });
            pageLog.info('   ✅ Slider encontrado');
        } catch (e) {
            pageLog.info('   ⚠️ Aguardando mais...');
            await page.waitForTimeout(10000);
        }
        
        const info = await page.evaluate(() => {
            const slider = document.getElementById('platts-topNews-slider');
            const links = slider ? slider.querySelectorAll('a[href*="insightsArticle"]') : [];
            return { hasSlider: !!slider, linkCount: links.length };
        });
        
        pageLog.info(`   Slider: ${info.hasSlider}, Links: ${info.linkCount}`);
        return info.hasSlider && info.linkCount > 0;
        
    } catch (error) {
        pageLog.error(`Erro Ferrous Metals: ${error.message}`);
        return false;
    }
}

/**
 * Coletar Top News - SEM FILTRO DE DATA (filtra depois de entrar no artigo)
 */
async function collectTopNewsList(page, pageLog, maxToCheck = 5) {
    try {
        pageLog.info(`⭐ Coletando Top News (verificar até ${maxToCheck})...`);
        
        await closePopups(page, pageLog);
        await page.waitForTimeout(2000);
        
        const topNewsList = await page.evaluate((max) => {
            const slider = document.getElementById('platts-topNews-slider');
            if (!slider) return [];
            
            const links = slider.querySelectorAll('a[href*="insightsArticle"]');
            const news = [];
            
            links.forEach((link, index) => {
                if (index < max) {
                    news.push({
                        index: index,
                        title: link.textContent.trim().substring(0, 200),
                        href: link.href || '',
                        date: '',
                        source: 'Top News - Ferrous Metals',
                        clickMethod: 'href'
                    });
                }
            });
            
            return news;
        }, maxToCheck);
        
        pageLog.info(`   📋 ${topNewsList.length} Top News para verificar`);
        
        topNewsList.forEach((item, i) => {
            pageLog.info(`   [${i}] "${item.title.substring(0, 50)}..."`);
        });
        
        return topNewsList;
        
    } catch (error) {
        pageLog.error(`Erro Top News: ${error.message}`);
        return [];
    }
}

/**
 * Navegar para Iron Ore
 */
async function navigateToIronOre(page, pageLog) {
    try {
        pageLog.info('🧭 Navegando para Iron Ore...');
        
        await page.goto('https://plattsconnect.spglobal.com/#platts/topic?menuserviceline=Ferrous%20Metals&serviceline=Steel%20%26%20Raw%20Materials&topic=Iron%20Ore', {
            waitUntil: 'domcontentloaded', timeout: 30000
        });
        
        await page.waitForTimeout(10000);
        await closePopups(page, pageLog);
        
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

/**
 * Coletar News & Insights
 */
async function collectNewsList(page, pageLog, maxArticles, dateFilter, daysBack) {
    try {
        pageLog.info('📰 Coletando News & Insights...');
        
        await closePopups(page, pageLog);
        await page.waitForTimeout(3000);
        
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
                        clickMethod: 'elementId'
                    });
                } else break;
            }
            return news;
        });
        
        pageLog.info(`   📋 ${newsList.length} encontrados`);
        
        newsList.forEach((item, i) => {
            pageLog.info(`   [${i}] "${item.title.substring(0, 40)}..." | Data: "${item.date}"`);
        });
        
        // Filtrar
        const filtered = [];
        for (const item of newsList) {
            pageLog.info(`\n   Processando: "${item.title.substring(0, 30)}..."`);
            if (isDateWithinFilter(item.date, dateFilter, daysBack)) {
                filtered.push(item);
                if (filtered.length >= maxArticles) break;
            }
        }
        
        pageLog.info(`\n✅ ${filtered.length} passaram pelo filtro`);
        return filtered;
        
    } catch (error) {
        pageLog.error(`Erro News: ${error.message}`);
        return [];
    }
}

/**
 * Coletar Market Commentary
 */
async function collectMarketCommentaryList(page, pageLog, maxArticles, dateFilter, daysBack) {
    try {
        pageLog.info('📊 Coletando Market Commentary...');
        
        await page.waitForTimeout(2000);
        
        const marketList = await page.evaluate(() => {
            const items = [];
            for (let i = 0; i < 10; i++) {
                const el = document.getElementById(`market-commentary-title-${i}`);
                if (el) {
                    const container = el.parentElement;
                    let date = '';
                    if (container) {
                        const text = container.innerText || '';
                        const match = text.match(/\d{2}\/\d{2}\/\d{4}\s+\d{2}:\d{2}:\d{2}\s+UTC/);
                        if (match) date = match[0];
                    }
                    items.push({
                        index: i,
                        title: el.textContent.trim(),
                        date: date,
                        elementId: `market-commentary-title-${i}`,
                        source: 'Market Commentary',
                        clickMethod: 'elementId'
                    });
                } else break;
            }
            return items;
        });
        
        pageLog.info(`   📋 ${marketList.length} encontrados`);
        
        marketList.forEach((item, i) => {
            pageLog.info(`   [${i}] "${item.title.substring(0, 40)}..." | Data: "${item.date}"`);
        });
        
        // Filtrar
        const filtered = [];
        for (const item of marketList) {
            pageLog.info(`\n   Processando: "${item.title.substring(0, 30)}..."`);
            if (!item.date) {
                pageLog.warning(`   ⚠️ Sem data - pulando`);
                continue;
            }
            if (isDateWithinFilter(item.date, dateFilter, daysBack)) {
                filtered.push(item);
                if (filtered.length >= maxArticles) break;
            }
        }
        
        pageLog.info(`\n✅ ${filtered.length} passaram pelo filtro`);
        return filtered;
        
    } catch (error) {
        pageLog.error(`Erro Market: ${error.message}`);
        return [];
    }
}

/**
 * Coletar conteúdo do artigo (ATUALIZADO COM IMAGENS)
 */
async function collectArticleContent(page, pageLog, item, returnUrl = null, collectImages = false) {
    try {
        pageLog.info(`📖 Entrando: ${item.title.substring(0, 50)}...`);
        
        await closePopups(page, pageLog);
        
        // Navegar
        if (item.clickMethod === 'href' && item.href) {
            await page.goto(item.href, { waitUntil: 'domcontentloaded', timeout: 30000 });
        } else if (item.elementId) {
            try {
                await page.click(`#${item.elementId}`, { timeout: 5000 });
            } catch (e) {
                await page.click(`#${item.elementId}`, { force: true, timeout: 5000 });
            }
        }
        
        await page.waitForTimeout(8000);
        
        // Extrair conteúdo de texto
        const content = await page.evaluate(() => {
            const data = {
                title: '',
                author: '',
                publishDate: '',
                actualDate: '',
                paragraphs: [],
                fullText: '',
                metadata: {}
            };
            
            const h1 = document.querySelector('h1, .newsSection-headline');
            if (h1) data.title = h1.textContent.trim();
            
            // Autor - melhorado para pegar nome completo
            const authorLink = document.querySelector('.author-data-email-link span');
            if (authorLink) {
                data.author = authorLink.textContent.trim();
            } else {
                const authorEl = document.querySelector('[class*="author"], .publisher-header span');
                if (authorEl) data.author = authorEl.textContent.trim().replace('Author', '').replace('|', '').trim();
            }
            
            // Data do artigo
            const pageText = document.body.innerText;
            const dateMatch = pageText.match(/\d{2}\/\d{2}\/\d{4}\s+\d{2}:\d{2}:\d{2}\s+UTC/);
            if (dateMatch) {
                data.publishDate = dateMatch[0];
                data.actualDate = dateMatch[0];
            }
            
            // Highlights do artigo
            const highlightsEl = document.querySelector('.newsSection-highlights');
            if (highlightsEl) {
                const bullets = highlightsEl.querySelectorAll('li');
                data.metadata.highlights = Array.from(bullets).map(li => li.textContent.trim());
            }
            
            // Corpo do artigo
            const bodyEl = document.querySelector('.newsSection-body, .platts-newsSection-article, article');
            if (bodyEl) {
                bodyEl.querySelectorAll('p').forEach(p => {
                    const text = p.textContent.trim();
                    if (text.length > 20) data.paragraphs.push(text);
                });
                data.fullText = data.paragraphs.join('\n\n');
            }
            
            data.metadata.wordCount = data.fullText.split(/\s+/).filter(w => w).length;
            data.metadata.paragraphCount = data.paragraphs.length;
            
            // Extração de dados financeiros
            const prices = data.fullText.match(/\$[\d,]+\.?\d*\s*(billion|million)?/gi);
            if (prices) data.metadata.prices = [...new Set(prices)];
            
            const percents = data.fullText.match(/[\d]+\.?\d*%/g);
            if (percents) data.metadata.percentages = [...new Set(percents)];
            
            // Commodities/unidades
            const commodityPrices = data.fullText.match(/\$[\d,.]+\/(?:mt|ton|tonne|dmt|kg|lb)/gi);
            if (commodityPrices) data.metadata.commodityPrices = [...new Set(commodityPrices)];
            
            const companies = ['US Steel', 'Nippon Steel', 'Vale', 'Rio Tinto', 'BHP', 
                'ArcelorMittal', 'Fortescue', 'Anglo American', 'POSCO', 'Baowu',
                'Cleveland-Cliffs', 'Nucor', 'Tata Steel', 'JFE', 'Shougang'];
            data.metadata.companies = companies.filter(c => data.fullText.toLowerCase().includes(c.toLowerCase()));
            
            return data;
        });
        
        // ========== EXTRAÇÃO DE IMAGENS ==========
        if (collectImages) {
            // Gerar ID do artigo
            const articleId = item.href?.match(/articleID=([^&]+)/)?.[1] || 
                             item.href?.match(/newsArticle\?articleID=([^&]+)/)?.[1] ||
                             `article_${Date.now()}`;
            
            // Extrair imagens
            const imagesData = await extractArticleImages(page, pageLog);
            
            // Salvar imagens no Key-Value Store
            let savedImages = [];
            if (imagesData.length > 0) {
                savedImages = await saveImagesToStore(imagesData, articleId, pageLog);
            }
            
            // Adicionar ao content
            content.images = {
                articleId: articleId,
                total: imagesData.length,
                thumbnail: savedImages.find(i => i.type === 'thumbnail') || null,
                charts: savedImages.filter(i => i.type === 'chart'),
                savedFiles: savedImages.map(i => i.filename)
            };
            
            content.metadata.imageCount = imagesData.length;
            content.metadata.chartCount = imagesData.filter(i => i.type === 'chart').length;
            content.metadata.thumbnailSaved = savedImages.some(i => i.type === 'thumbnail');
        }
        // ========== FIM EXTRAÇÃO DE IMAGENS ==========
        
        // Atualizar data do item
        if (content.actualDate) {
            item.date = content.actualDate;
            pageLog.info(`   📅 Data do artigo: ${content.actualDate}`);
        }
        
        const imageInfo = collectImages ? `, ${content.metadata.imageCount || 0} imagens` : '';
        pageLog.info(`   ✅ ${content.metadata.wordCount} palavras${imageInfo}`);
        
        // Voltar
        if (returnUrl) {
            await page.goto(returnUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
        } else {
            await page.goBack();
        }
        
        await page.waitForTimeout(5000);
        await closePopups(page, pageLog);
        
        return { ...item, ...content, extractedAt: new Date().toISOString() };
        
    } catch (error) {
        pageLog.error(`Erro artigo: ${error.message}`);
        try {
            if (returnUrl) await page.goto(returnUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
            else await page.goBack();
        } catch (e) {}
        return null;
    }
}

// ========== MAIN ==========

const input = await Actor.getInput() ?? {};

log.info('🔍 Input:');
log.info(JSON.stringify(input, null, 2));

const {
    username,
    password,
    maxArticles = 10,
    collectTopNews = true,
    collectMarketCommentary = true,
    collectImages = false,  // NOVO: parâmetro para coleta de imagens
    dateFilter = 'today',
    daysToCollect = 1,
    targetDate = null,
    maxTopNewsToCheck = 5
} = input;

globalTargetDate = targetDate;

log.info('=====================================');
log.info('Platts Iron Ore Scraper v5.9');
log.info('COM SUPORTE A IMAGENS');
log.info('=====================================');
log.info(`Data servidor: ${new Date().toISOString()}`);
log.info(`Data alvo: ${targetDate || 'hoje'}`);
log.info(`Max artigos: ${maxArticles}`);
log.info(`Max Top News verificar: ${maxTopNewsToCheck}`);
log.info(`Filtro: ${dateFilter}`);
log.info(`Coletar imagens: ${collectImages ? 'SIM' : 'NÃO'}`);
log.info('=====================================');

if (!username || !password) {
    await Actor.pushData({ type: 'error', message: 'Credenciais necessárias' });
    await Actor.exit();
}

const crawler = new PlaywrightCrawler({
    launchContext: {
        launchOptions: { headless: true, args: ['--no-sandbox'] },
        useChrome: true
    },
    maxRequestRetries: 0,
    requestHandlerTimeoutSecs: 900,
    navigationTimeoutSecs: 60,

    async requestHandler({ page, log: pageLog }) {
        try {
            page.setDefaultTimeout(30000);
            
            // LOGIN
            pageLog.info('🔐 Login...');
            if (!await loginPlatts(page, username, password, pageLog)) {
                throw new Error('Login falhou');
            }
            pageLog.info('✅ Login OK!');
            
            const articles = [];
            
            // TOP NEWS
            if (collectTopNews) {
                pageLog.info('\n========== TOP NEWS ==========');
                
                if (await navigateToFerrousMetals(page, pageLog)) {
                    const topNewsList = await collectTopNewsList(page, pageLog, maxTopNewsToCheck);
                    const returnUrl = 'https://plattsconnect.spglobal.com/#platts/allInsights?keySector=Ferrous%20Metals';
                    
                    for (let i = 0; i < topNewsList.length; i++) {
                        const item = topNewsList[i];
                        pageLog.info(`\n⭐ [${i + 1}/${topNewsList.length}] Verificando Top News...`);
                        
                        // Entrar no artigo para pegar a data E IMAGENS
                        const content = await collectArticleContent(page, pageLog, item, returnUrl, collectImages);
                        
                        if (content && content.fullText) {
                            // FILTRAR AGORA que temos a data real
                            pageLog.info(`   Aplicando filtro de data...`);
                            if (dateFilter === 'all' || isDateWithinFilter(content.actualDate, dateFilter, daysToCollect)) {
                                articles.push(content);
                                pageLog.info(`   ✅ INCLUÍDO!`);
                            } else {
                                pageLog.info(`   ⏭️ Fora do filtro - não incluído`);
                            }
                        }
                        
                        await page.waitForTimeout(2000);
                        
                        if (articles.length >= maxArticles) {
                            pageLog.info(`   🛑 Limite de ${maxArticles} atingido`);
                            break;
                        }
                    }
                } else {
                    pageLog.warning('⚠️ Top News não disponível');
                }
            }
            
            // IRON ORE
            if (articles.length < maxArticles) {
                pageLog.info('\n========== IRON ORE ==========');
                
                if (await navigateToIronOre(page, pageLog)) {
                    // News & Insights
                    const remaining = maxArticles - articles.length;
                    const newsList = await collectNewsList(page, pageLog, remaining, dateFilter, daysToCollect);
                    
                    for (let i = 0; i < newsList.length && articles.length < maxArticles; i++) {
                        pageLog.info(`\n📰 [${i + 1}/${newsList.length}] News & Insights`);
                        const content = await collectArticleContent(page, pageLog, newsList[i], null, collectImages);
                        if (content?.fullText) articles.push(content);
                        await page.waitForTimeout(2000);
                    }
                    
                    // Market Commentary
                    if (collectMarketCommentary && articles.length < maxArticles) {
                        const remainingMkt = maxArticles - articles.length;
                        const mktList = await collectMarketCommentaryList(page, pageLog, remainingMkt, dateFilter, daysToCollect);
                        
                        for (let i = 0; i < mktList.length && articles.length < maxArticles; i++) {
                            pageLog.info(`\n📊 [${i + 1}/${mktList.length}] Market Commentary`);
                            const content = await collectArticleContent(page, pageLog, mktList[i], null, collectImages);
                            if (content?.fullText) articles.push(content);
                            await page.waitForTimeout(2000);
                        }
                    }
                }
            }
            
            // RESULTADO
            if (articles.length === 0) {
                pageLog.warning(`Nenhuma notícia para ${globalTargetDate || 'hoje'}`);
                await Actor.pushData({
                    type: 'no_data',
                    message: `Nenhuma notícia para ${globalTargetDate || 'hoje'}`,
                    dateFilter, targetDate: globalTargetDate,
                    timestamp: new Date().toISOString()
                });
                return;
            }
            
            const topNews = articles.filter(a => a.source.includes('Top News'));
            const news = articles.filter(a => a.source === 'News & Insights');
            const market = articles.filter(a => a.source === 'Market Commentary');
            
            // Contagem de imagens
            const totalImages = articles.reduce((sum, a) => sum + (a.images?.total || 0), 0);
            const totalCharts = articles.reduce((sum, a) => sum + (a.images?.charts?.length || 0), 0);
            const allSavedFiles = articles.flatMap(a => a.images?.savedFiles || []);
            
            const result = {
                type: 'success',
                dateFilter, 
                targetDate: globalTargetDate,
                collectImages: collectImages,
                summary: {
                    totalArticles: articles.length,
                    topNews: topNews.length,
                    newsInsights: news.length,
                    marketCommentary: market.length,
                    totalWords: articles.reduce((s, a) => s + (a.metadata?.wordCount || 0), 0),
                    // Métricas de imagens
                    totalImages: totalImages,
                    totalCharts: totalCharts,
                    savedImageFiles: allSavedFiles,
                    // Outras métricas
                    companiesMentioned: [...new Set(articles.flatMap(a => a.metadata?.companies || []))],
                    pricesFound: [...new Set(articles.flatMap(a => a.metadata?.prices || []))]
                },
                topNews, 
                newsInsights: news, 
                marketCommentary: market,
                allArticles: articles,
                extractedAt: new Date().toISOString()
            };
            
            await Actor.pushData(result);
            
            log.info('\n=====================================');
            log.info('📊 RESUMO');
            log.info(`✅ Total: ${articles.length}`);
            log.info(`   ⭐ Top News: ${topNews.length}`);
            log.info(`   📰 News: ${news.length}`);
            log.info(`   📊 Market: ${market.length}`);
            if (collectImages) {
                log.info(`   🖼️ Imagens: ${totalImages} (${totalCharts} gráficos)`);
                log.info(`   💾 Arquivos salvos: ${allSavedFiles.length}`);
            }
            log.info('=====================================');
            
        } catch (error) {
            pageLog.error(`❌ ${error.message}`);
            await Actor.pushData({ type: 'error', error: error.message, timestamp: new Date().toISOString() });
            throw error;
        }
    }
});

await crawler.run(['about:blank']);
log.info('🏁 Fim!');
await Actor.exit();