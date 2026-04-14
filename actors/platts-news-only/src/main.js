/**
 * Platts IODEX Commentary and Rationale Scraper v15.0
 * 
 * Correções vs v14:
 * - Extrai conteúdo de div.readingpane-details (inline reading pane) em vez de .ant-drawer
 * - Clica no button.ag-anchor (role="link") para carregar artigo na reading pane
 * - Fecha sidebar Actions se estiver aberto antes de extrair
 * - Auto-detecção do formato de data do grid (DD/MM/YYYY vs MM/DD/YYYY)
 * - Seleção robusta do grid de notícias vs grid de preços
 * - Colunas detectadas dinamicamente
 * 
 * Input:
 * - username: string (obrigatório)
 * - password: string (obrigatório)  
 * - targetDate: string no formato 'DD/MM/YYYY' (brasileiro) ou 'MM/DD/YYYY' (americano)
 * - dateFormat: string 'BR' ou 'US' (opcional - padrão: 'BR')
 * - maxArticles: number (padrão: 2)
 */

import { PlaywrightCrawler, log } from 'crawlee';
import { Actor } from 'apify';

await Actor.init();

// ========== UTILIDADES DE DATA ==========

/**
 * Gera ambos os formatos de data a partir do input do usuário
 */
function parseDateInput(dateStr, inputFormat = 'BR') {
    if (!dateStr || !dateStr.includes('/')) {
        const today = new Date();
        const dd = String(today.getUTCDate()).padStart(2, '0');
        const mm = String(today.getUTCMonth() + 1).padStart(2, '0');
        const yyyy = today.getUTCFullYear();
        return {
            br: `${dd}/${mm}/${yyyy}`,
            us: `${mm}/${dd}/${yyyy}`,
            day: dd,
            month: mm,
            year: String(yyyy)
        };
    }

    const parts = dateStr.split('/');
    if (parts.length !== 3) {
        throw new Error(`Formato de data inválido: ${dateStr}`);
    }

    let day, month, year;
    if (inputFormat === 'BR') {
        [day, month, year] = parts;
    } else {
        [month, day, year] = parts;
    }

    day = day.padStart(2, '0');
    month = month.padStart(2, '0');

    return {
        br: `${day}/${month}/${year}`,
        us: `${month}/${day}/${year}`,
        day,
        month,
        year
    };
}

// ========== LOGIN ==========

async function loginPlatts(page, username, password, pageLog) {
    try {
        pageLog.info('Navegando para página inicial...');
        await page.goto('https://www.spglobal.com/commodityinsights/en', {
            waitUntil: 'domcontentloaded',
            timeout: 30000
        });
        await page.waitForTimeout(3000);

        pageLog.info('Navegando para página de login...');
        await page.goto('https://www.spglobal.com/bin/commodityinsights/login', {
            waitUntil: 'networkidle',
            timeout: 30000
        });
        await page.waitForTimeout(10000);

        const afterLoginUrl = page.url();
        if (afterLoginUrl.includes('commodity-insights')) {
            await page.goto('https://www.spglobal.com/bin/commodityinsights/login', {
                waitUntil: 'networkidle',
                timeout: 30000
            });
            await page.waitForTimeout(5000);
        }

        pageLog.info('Preenchendo username...');
        await page.waitForSelector('input[name="identifier"]', {
            timeout: 20000,
            state: 'visible'
        });

        await page.click('input[name="identifier"]');
        await page.waitForTimeout(500);
        await page.fill('input[name="identifier"]', '');
        await page.type('input[name="identifier"]', username, { delay: 100 });

        pageLog.info('Clicando em Next...');
        await page.waitForTimeout(1000);

        const nextButton = await page.$('input[type="submit"][value="Next"]') ||
            await page.$('button:has-text("Next")') ||
            await page.$('input[type="submit"]');

        if (nextButton) {
            await nextButton.click();
        } else {
            await page.keyboard.press('Enter');
        }

        await page.waitForTimeout(5000);

        const hasMethodPage = await page.$('div[data-se="okta_password"]');
        if (hasMethodPage) {
            pageLog.info('Selecionando método de autenticação por senha...');

            const selectPasswordSelectors = [
                'div[data-se="okta_password"] a.select-factor',
                'div[data-se="okta_password"] a:has-text("Select")',
                'a[aria-label="Select Password."]'
            ];

            let clicked = false;
            for (const selector of selectPasswordSelectors) {
                try {
                    await page.click(selector, { timeout: 3000 });
                    clicked = true;
                    break;
                } catch (e) {
                    continue;
                }
            }

            if (!clicked) {
                await page.evaluate(() => {
                    const passwordDiv = document.querySelector('div[data-se="okta_password"]');
                    if (passwordDiv) {
                        const selectLink = passwordDiv.querySelector('a.select-factor');
                        if (selectLink) {
                            selectLink.click();
                        }
                    }
                });
            }

            await page.waitForTimeout(5000);
        }

        pageLog.info('Preenchendo senha...');
        await page.waitForSelector('input[type="password"], input[name="credentials.passcode"]', {
            timeout: 15000
        });

        const passwordField = await page.$('input[name="credentials.passcode"]') ||
            await page.$('input[type="password"]');

        await passwordField.click();
        await passwordField.fill(password);

        await page.waitForTimeout(1000);
        const verifyButton = await page.$('input[type="submit"][value="Verify"]') ||
            await page.$('button:has-text("Verify")') ||
            await page.$('input[type="submit"]');

        if (verifyButton) {
            await verifyButton.click();
        } else {
            await page.keyboard.press('Enter');
        }

        pageLog.info('Aguardando autenticação...');
        await page.waitForTimeout(15000);

        pageLog.info(`Login concluído. URL: ${page.url()}`);
        return true;

    } catch (error) {
        pageLog.error(`Erro no login: ${error.message}`);
        return false;
    }
}

// ========== NAVEGAÇÃO ==========

async function navigateToRawMaterials(page, pageLog) {
    try {
        pageLog.info('Navegando para Raw Materials Workspace...');

        const currentUrl = page.url();

        if (!currentUrl.includes('plattsconnect.spglobal.com')) {
            pageLog.info('Navegando para Platts Connect...');

            if (currentUrl.includes('commodity-insights')) {
                const plattsLinkClicked = await page.evaluate(() => {
                    const links = document.querySelectorAll('a');
                    for (const link of links) {
                        if (link.href && link.href.includes('plattsconnect.spglobal.com')) {
                            link.click();
                            return true;
                        }
                        if (link.innerText && (link.innerText.includes('Platts Connect') ||
                            link.innerText.includes('Go to Platts'))) {
                            link.click();
                            return true;
                        }
                    }
                    return false;
                });

                if (plattsLinkClicked) {
                    await page.waitForTimeout(10000);
                } else {
                    await page.goto('https://plattsconnect.spglobal.com/#platts/landingpage', {
                        waitUntil: 'domcontentloaded',
                        timeout: 30000
                    });
                    await page.waitForTimeout(10000);
                }
            }
        }

        await page.waitForTimeout(5000);

        const newUrl = page.url();
        if (!newUrl.includes('plattsconnect.spglobal.com')) {
            pageLog.warning('Ainda não está no Platts Connect, tentando novamente...');
            await page.goto('https://plattsconnect.spglobal.com/#platts/landingpage', {
                waitUntil: 'domcontentloaded',
                timeout: 30000
            });
            await page.waitForTimeout(10000);
        }

        pageLog.info('Procurando botão Raw Materials...');
        await page.waitForTimeout(5000);

        const rawMaterialsClicked = await page.evaluate(() => {
            const button = document.getElementById('default-link-button-4');
            if (button && button.innerText && button.innerText.includes('Raw Materials')) {
                button.click();
                return true;
            }

            const buttons = document.querySelectorAll('button');
            for (const btn of buttons) {
                if (btn.innerText && btn.innerText.includes('Raw Materials Workspace')) {
                    btn.click();
                    return true;
                }
            }

            const elements = document.querySelectorAll('*');
            for (const el of elements) {
                if (el.innerText && el.innerText === 'Raw Materials Workspace') {
                    el.click();
                    return true;
                }
            }

            return false;
        });

        if (rawMaterialsClicked) {
            pageLog.info('Raw Materials Workspace clicado');
            await page.waitForTimeout(25000);
        } else {
            pageLog.warning('Navegação direta para Raw Materials...');
            await page.goto('https://plattsconnect.spglobal.com/#platts/workspace?workspace=Raw%20Materials%20Workspace&type=public', {
                waitUntil: 'domcontentloaded',
                timeout: 30000
            });
            await page.waitForTimeout(20000);
        }

        return true;

    } catch (error) {
        pageLog.error(`Erro ao navegar para Raw Materials: ${error.message}`);
        return false;
    }
}

// ========== DETECÇÃO DE DATA ==========

function detectDateFormat(sampleDates, pageLog) {
    if (!sampleDates || sampleDates.length === 0) {
        pageLog.warning('Sem amostras de data, assumindo DD/MM/YYYY');
        return 'DD/MM/YYYY';
    }

    let mustBeDDMM = false;
    let mustBeMMDD = false;

    for (const dateStr of sampleDates) {
        const match = dateStr.match(/^(\d{2})\/(\d{2})\/(\d{4})/);
        if (!match) continue;

        const part1 = parseInt(match[1]);
        const part2 = parseInt(match[2]);

        if (part1 > 12) mustBeDDMM = true;
        if (part2 > 12) mustBeMMDD = true;
    }

    if (mustBeDDMM && !mustBeMMDD) {
        pageLog.info('📅 Formato detectado: DD/MM/YYYY (parte1 > 12)');
        return 'DD/MM/YYYY';
    }

    if (mustBeMMDD && !mustBeDDMM) {
        pageLog.info('📅 Formato detectado: MM/DD/YYYY (parte2 > 12)');
        return 'MM/DD/YYYY';
    }

    // Ambíguo - comparar com data de hoje
    const today = new Date();
    const todayDay = today.getUTCDate();
    const todayMonth = today.getUTCMonth() + 1;

    const firstDate = sampleDates[0];
    const match = firstDate.match(/^(\d{2})\/(\d{2})\/(\d{4})/);
    if (match) {
        const p1 = parseInt(match[1]);
        const p2 = parseInt(match[2]);

        if (p1 === todayDay && p2 === todayMonth) {
            pageLog.info(`📅 Formato inferido: DD/MM/YYYY (${p1}=dia_hoje, ${p2}=mês_hoje)`);
            return 'DD/MM/YYYY';
        }
        if (p1 === todayMonth && p2 === todayDay) {
            pageLog.info(`📅 Formato inferido: MM/DD/YYYY (${p1}=mês_hoje, ${p2}=dia_hoje)`);
            return 'MM/DD/YYYY';
        }
    }

    pageLog.warning('📅 Formato ambíguo, usando fallback: DD/MM/YYYY');
    return 'DD/MM/YYYY';
}

// ========== IDENTIFICAÇÃO DO GRID ==========

async function identifyNewsGrid(page, pageLog) {
    const analysis = await page.evaluate(() => {
        const grids = document.querySelectorAll('.ag-root-wrapper');
        const candidates = [];

        grids.forEach((grid, gi) => {
            if (grid.offsetParent === null) return;

            const rows = grid.querySelectorAll('.ag-row');
            if (rows.length === 0) return;

            const datePattern = /^\d{2}\/\d{2}\/\d{4}\s+\d{2}:\d{2}:\d{2}\s+UTC$/;

            let dateColumnIndex = -1;
            let titleColumnIndex = -1;
            let keyColumnIndex = -1;
            let sampleDates = [];

            for (let ri = 0; ri < Math.min(rows.length, 15); ri++) {
                const cells = rows[ri].querySelectorAll('.ag-cell');
                if (cells.length < 2) continue;

                for (let ci = 0; ci < cells.length; ci++) {
                    const text = cells[ci]?.innerText?.trim() || '';
                    if (!text) continue;

                    if (dateColumnIndex === -1 && datePattern.test(text)) {
                        dateColumnIndex = ci;
                    }

                    if (keyColumnIndex === -1 && /^PMA\d+/.test(text)) {
                        keyColumnIndex = ci;
                    }
                }

                if (dateColumnIndex >= 0) {
                    const dateText = cells[dateColumnIndex]?.innerText?.trim() || '';
                    if (/\d{2}\/\d{2}\/\d{4}/.test(dateText)) {
                        sampleDates.push(dateText);
                    }

                    // Coluna do título = tem button.ag-anchor dentro
                    if (titleColumnIndex === -1) {
                        for (let ci = 0; ci < cells.length; ci++) {
                            const anchor = cells[ci]?.querySelector('button.ag-anchor');
                            if (anchor) {
                                titleColumnIndex = ci;
                                break;
                            }
                        }
                    }

                    // Fallback: coluna com texto longo que não é data nem key
                    if (titleColumnIndex === -1) {
                        for (let ci = 0; ci < cells.length; ci++) {
                            if (ci !== dateColumnIndex && ci !== keyColumnIndex) {
                                const text = cells[ci]?.innerText?.trim() || '';
                                if (text.length > 20) {
                                    titleColumnIndex = ci;
                                    break;
                                }
                            }
                        }
                    }
                }
            }

            if (dateColumnIndex >= 0 && sampleDates.length > 0) {
                candidates.push({
                    gridIndex: gi,
                    rowCount: rows.length,
                    dateColumnIndex,
                    titleColumnIndex,
                    keyColumnIndex,
                    sampleDates: sampleDates.slice(0, 5),
                    cellCount: rows[0]?.querySelectorAll('.ag-cell').length || 0
                });
            }
        });

        return candidates;
    });

    if (analysis.length === 0) {
        pageLog.error('❌ Nenhum grid de notícias encontrado');
        return null;
    }

    const best = analysis.sort((a, b) => b.sampleDates.length - a.sampleDates.length)[0];

    pageLog.info(`✅ Grid de notícias: Grid ${best.gridIndex} (${best.rowCount} linhas)`);
    pageLog.info(`   Colunas: key=${best.keyColumnIndex}, título=${best.titleColumnIndex}, data=${best.dateColumnIndex}`);
    pageLog.info(`   Datas amostra: ${best.sampleDates.slice(0, 3).join(' | ')}`);

    best.detectedFormat = detectDateFormat(best.sampleDates, pageLog);
    return best;
}

// ========== EXTRAÇÃO DA READING PANE (NOVO v15) ==========

/**
 * Extrai o conteúdo do artigo da reading pane inline
 * A reading pane carrega ao lado do grid quando se clica num artigo
 */
async function extractFromReadingPane(page, pageLog) {
    const content = await page.evaluate(() => {
        // Seletores para a reading pane (baseado na investigação do DOM)
        const paneSelectors = [
            '.readingpane-details article',
            '.platts-inArticle-newsArticle-detailsSection article',
            '.readingpane-details',
            '.platts-inArticle-newsArticle-detailsSection'
        ];

        let pane = null;
        for (const selector of paneSelectors) {
            pane = document.querySelector(selector);
            if (pane && pane.innerText && pane.innerText.length > 100) {
                break;
            }
        }

        if (!pane) {
            return { success: false, error: 'Reading pane não encontrada' };
        }

        const data = {
            success: true,
            title: '',
            author: '',
            authors: [],
            publishDate: '',
            topics: [],
            highlights: [],
            paragraphs: [],
            fullText: '',
            metadata: {}
        };

        // Extrair título do h1
        const h1 = pane.querySelector('h1');
        data.title = h1?.innerText?.trim() || '';

        // Extrair data de publicação do span com padrão de data
        const allSpans = pane.querySelectorAll('span');
        for (const span of allSpans) {
            const text = span.innerText?.trim() || '';
            if (/\d{2}\/\d{2}\/\d{4}/.test(text)) {
                data.publishDate = text;
                break;
            }
        }

        // Extrair autores dos links de email
        const authorLinks = pane.querySelectorAll('a.auther-data-email-link, a[href^="mailto:"]');
        authorLinks.forEach(link => {
            const name = link.innerText?.trim();
            if (name && name.length > 1 && name !== '|') {
                data.authors.push(name);
            }
        });
        data.author = data.authors.join(', ');

        // Extrair parágrafos
        const allParagraphs = pane.querySelectorAll('p');
        const bodyParagraphs = [];
        const highlights = [];

        allParagraphs.forEach(p => {
            const text = p.innerText?.trim();
            if (!text || text.length < 10) return;
            if (text.includes('Cookie') || text.includes('Privacy') || text.includes('Terms of Service')) return;

            // Os primeiros parágrafos curtos antes do corpo principal são highlights/bullet points
            // O corpo começa com parágrafos mais longos (>80 chars geralmente)
            if (bodyParagraphs.length === 0 && text.length < 80 && !text.includes('.')) {
                highlights.push(text);
            } else {
                bodyParagraphs.push(text);
            }
        });

        data.highlights = highlights;
        data.paragraphs = bodyParagraphs;
        data.fullText = bodyParagraphs.join('\n\n');

        // Metadados
        data.metadata.wordCount = data.fullText.split(/\s+/).filter(w => w).length;
        data.metadata.paragraphCount = bodyParagraphs.length;
        data.metadata.highlightCount = highlights.length;

        // Preços mencionados
        const priceMatches = data.fullText.match(/\$[\d,]+\.?\d*/g);
        if (priceMatches) {
            data.metadata.prices = [...new Set(priceMatches)];
        }

        // Yuan mencionados
        const yuanMatches = data.fullText.match(/Yuan\s*[\d,]+\.?\d*/gi);
        if (yuanMatches) {
            data.metadata.yuanPrices = [...new Set(yuanMatches)];
        }

        // Percentagens
        const percentMatches = data.fullText.match(/[\d]+\.?\d*%/g);
        if (percentMatches) {
            data.metadata.percentages = [...new Set(percentMatches)];
        }

        // Empresas
        const companies = [
            'Vale', 'Rio Tinto', 'BHP', 'FMG', 'Fortescue',
            'Cargill', 'Trafigura', 'Anglo American', 'CSN',
            'ArcelorMittal', 'Baosteel', 'POSCO', 'CMRG'
        ];
        data.metadata.companies = companies.filter(company =>
            data.fullText.toLowerCase().includes(company.toLowerCase())
        );

        // Assessments IODEX
        const iodexMatch = data.fullText.match(/IODEX at \$([\d.]+)/);
        if (iodexMatch) {
            data.metadata.iodexPrice = iodexMatch[1];
        }

        // IOPEX prices
        const iopexMatches = data.fullText.match(/IOPEX[^.]+/g);
        if (iopexMatches) {
            data.metadata.iopexAssessments = iopexMatches;
        }

        // Lump premium
        const lumpMatch = data.fullText.match(/lump premium at ([\d.]+ cents\/dmtu)/i);
        if (lumpMatch) {
            data.metadata.lumpPremium = lumpMatch[1];
        }

        return data;
    });

    return content;
}

// ========== EXTRAÇÃO PRINCIPAL v15 ==========

async function extractSpecificDateNews(page, pageLog, dateInfo, maxArticles = 2) {
    try {
        // PASSO 1: Aguardar carregamento
        pageLog.info('⏳ Aguardando carregamento inicial dos widgets...');
        await page.waitForTimeout(12000);

        // PASSO 2: Polling de carregamento
        pageLog.info('🔄 Verificando carregamento completo...');
        let pageLoaded = false;
        let loadAttempts = 0;

        while (!pageLoaded && loadAttempts < 10) {
            loadAttempts++;
            const pageStatus = await page.evaluate(() => {
                const grids = document.querySelectorAll('.ag-root-wrapper');
                const tabs = document.querySelectorAll('[id*="widget-area-tab"]');
                return { gridsCount: grids.length, tabsCount: tabs.length };
            });

            pageLog.info(`  Tentativa ${loadAttempts}: Grids: ${pageStatus.gridsCount}, Tabs: ${pageStatus.tabsCount}`);

            if (pageStatus.gridsCount > 0 || pageStatus.tabsCount > 5) {
                pageLoaded = true;
                pageLog.info('✅ Página carregada');
            } else {
                await page.waitForTimeout(3000);
            }
        }

        // PASSO 3: Clicar na tab IODEX Commentary and Rationale
        pageLog.info('📑 Clicando na tab IODEX Commentary and Rationale...');

        const iodexTabClicked = await page.evaluate(() => {
            let iodexTab = document.getElementById('2-widget-area-tab-IODEX Commentary and Rationale');

            if (!iodexTab) {
                const allTabs = document.querySelectorAll('[id*="widget-area-tab"]');
                for (const tab of allTabs) {
                    if (tab.innerText && tab.innerText.includes('IODEX Commentary and Rationale')) {
                        iodexTab = tab;
                        break;
                    }
                }
            }

            if (!iodexTab) {
                return { success: false, message: 'Tab não encontrada' };
            }

            const isActive = iodexTab.getAttribute('aria-selected') === 'true';
            if (!isActive) {
                iodexTab.click();
                const spanLabel = iodexTab.querySelector('.tab-label');
                if (spanLabel) spanLabel.click();
            }

            return { success: true, message: isActive ? 'Já ativa' : 'Clicada' };
        });

        pageLog.info(`Tab: ${iodexTabClicked.message}`);

        if (!iodexTabClicked.success) {
            pageLog.error('❌ Tab IODEX Commentary não encontrada');
            return [];
        }

        pageLog.info('⏳ Aguardando grid carregar...');
        await page.waitForTimeout(15000);

        // PASSO 4: Fechar sidebar Actions se estiver aberto
        pageLog.info('🧹 Verificando e fechando sidebar Actions...');
        await page.evaluate(() => {
            const closeBtn = document.querySelector('#Actions-sidebar-close-model');
            if (closeBtn) {
                closeBtn.click();
            }
        });
        await page.waitForTimeout(1000);

        // PASSO 5: Identificar grid de notícias e formato de data
        pageLog.info('🔍 Identificando grid de notícias...');
        const gridInfo = await identifyNewsGrid(page, pageLog);

        if (!gridInfo) {
            pageLog.error('❌ Grid de notícias não encontrado');
            return [];
        }

        // PASSO 6: Determinar string de busca no formato correto
        const searchDate = gridInfo.detectedFormat === 'DD/MM/YYYY' ? dateInfo.br : dateInfo.us;
        pageLog.info(`🎯 Buscando: "${searchDate}" (formato grid: ${gridInfo.detectedFormat})`);

        // PASSO 7: Buscar notícias da data
        const newsForDate = await page.evaluate((params) => {
            const { gridIndex, dateColIdx, titleColIdx, keyColIdx, searchDate } = params;
            const grids = document.querySelectorAll('.ag-root-wrapper');
            const targetGrid = grids[gridIndex];

            if (!targetGrid || targetGrid.offsetParent === null) {
                return { found: false, message: 'Grid não encontrado' };
            }

            const rows = targetGrid.querySelectorAll('.ag-row');
            const matchingNews = [];
            const allNews = [];

            for (let i = 0; i < rows.length; i++) {
                const cells = rows[i].querySelectorAll('.ag-cell');
                if (cells.length < 2) continue;

                const dateTime = dateColIdx >= 0 ? (cells[dateColIdx]?.innerText?.trim() || '') : '';
                const title = titleColIdx >= 0 ? (cells[titleColIdx]?.innerText?.trim() || '') : '';
                const key = keyColIdx >= 0 ? (cells[keyColIdx]?.innerText?.trim() || '') : '';

                if (!dateTime && !title) continue;

                const newsItem = { rowIndex: i, key, title, dateTime };

                if (dateTime || title) allNews.push(newsItem);
                if (dateTime.includes(searchDate)) matchingNews.push(newsItem);
            }

            return {
                found: matchingNews.length > 0,
                matchingNews,
                recentNews: matchingNews.length === 0 ? allNews.slice(0, 5) : [],
                totalValidNews: allNews.length
            };
        }, {
            gridIndex: gridInfo.gridIndex,
            dateColIdx: gridInfo.dateColumnIndex,
            titleColIdx: gridInfo.titleColumnIndex,
            keyColIdx: gridInfo.keyColumnIndex,
            searchDate
        });

        pageLog.info(`📊 ${newsForDate.totalValidNews} notícias no grid, ${newsForDate.matchingNews?.length || 0} para ${searchDate}`);

        if (!newsForDate.found) {
            pageLog.warning(`⚠️ Nenhuma notícia para ${searchDate}`);
            newsForDate.recentNews.forEach(n => {
                pageLog.info(`  - ${n.dateTime}: ${n.title?.substring(0, 60)}...`);
            });
            return [];
        }

        pageLog.info(`✅ ${newsForDate.matchingNews.length} notícias encontradas`);

        // PASSO 8: Processar cada notícia
        const articles = [];
        const toProcess = newsForDate.matchingNews.slice(0, maxArticles);

        for (let i = 0; i < toProcess.length; i++) {
            const newsItem = toProcess[i];
            pageLog.info(`\n📰 [${i + 1}/${toProcess.length}] ${newsItem.title?.substring(0, 70)}...`);
            pageLog.info(`   Data: ${newsItem.dateTime} | Key: ${newsItem.key}`);

            // Clicar no button.ag-anchor dentro da célula do título
            const clickResult = await page.evaluate((params) => {
                const { gridIndex, rowIndex, titleColIdx } = params;
                const grids = document.querySelectorAll('.ag-root-wrapper');
                const targetGrid = grids[gridIndex];
                if (!targetGrid) return { success: false, error: 'Grid não encontrado' };

                const rows = targetGrid.querySelectorAll('.ag-row');
                const targetRow = rows[rowIndex];
                if (!targetRow) return { success: false, error: 'Linha não encontrada' };

                // Clicar na row primeiro (seleciona)
                targetRow.click();

                // Clicar no button.ag-anchor (o link do título)
                const titleCell = targetRow.querySelectorAll('.ag-cell')[titleColIdx];
                if (titleCell) {
                    const anchorBtn = titleCell.querySelector('button.ag-anchor');
                    if (anchorBtn) {
                        anchorBtn.click();
                        return { success: true, method: 'ag-anchor button' };
                    }

                    // Fallback: clicar na célula inteira
                    titleCell.click();
                    return { success: true, method: 'cell click' };
                }

                return { success: false, error: 'Célula de título não encontrada' };
            }, {
                gridIndex: gridInfo.gridIndex,
                rowIndex: newsItem.rowIndex,
                titleColIdx: gridInfo.titleColumnIndex
            });

            if (!clickResult.success) {
                pageLog.warning(`   ⚠️ Clique falhou: ${clickResult.error}`);
                continue;
            }

            pageLog.info(`   ✅ Clique: ${clickResult.method}`);

            // Aguardar reading pane carregar
            pageLog.info('   ⏳ Aguardando reading pane carregar...');

            let readingPaneReady = false;
            let paneAttempts = 0;

            while (!readingPaneReady && paneAttempts < 10) {
                paneAttempts++;
                await page.waitForTimeout(2000);

                const paneStatus = await page.evaluate(() => {
                    const pane = document.querySelector('.readingpane-details');
                    if (!pane) return { exists: false };

                    const paragraphs = pane.querySelectorAll('p');
                    const longParagraphs = Array.from(paragraphs).filter(p =>
                        p.innerText?.trim().length > 50
                    );

                    return {
                        exists: true,
                        textLength: pane.innerText?.length || 0,
                        paragraphCount: paragraphs.length,
                        longParagraphCount: longParagraphs.length,
                        hasTitle: !!pane.querySelector('h1')?.innerText?.trim()
                    };
                });

                if (paneStatus.exists && paneStatus.longParagraphCount > 2) {
                    readingPaneReady = true;
                    pageLog.info(`   ✅ Reading pane carregada (${paneStatus.longParagraphCount} parágrafos, ${paneStatus.textLength} chars)`);
                } else if (paneStatus.exists) {
                    pageLog.info(`   ⏳ Pane existe mas conteúdo parcial (${paneStatus.textLength} chars, ${paneStatus.longParagraphCount} parágrafos longos)`);
                }
            }

            if (!readingPaneReady) {
                pageLog.warning('   ⚠️ Reading pane não carregou completamente, tentando extrair mesmo assim...');
            }

            // Extrair conteúdo da reading pane
            const content = await extractFromReadingPane(page, pageLog);

            if (content && content.success && content.fullText) {
                articles.push({
                    title: content.title,
                    author: content.author,
                    authors: content.authors,
                    publishDate: content.publishDate,
                    highlights: content.highlights,
                    paragraphs: content.paragraphs,
                    fullText: content.fullText,
                    metadata: content.metadata,
                    gridKey: newsItem.key,
                    gridDateTime: newsItem.dateTime,
                    extractedAt: new Date().toISOString()
                });

                pageLog.info(`   ✅ Extraído: ${content.metadata.wordCount} palavras, ${content.metadata.paragraphCount} parágrafos`);

                if (content.highlights?.length > 0) {
                    pageLog.info(`   📌 Highlights: ${content.highlights.join(' | ')}`);
                }

                if (content.metadata.iodexPrice) {
                    pageLog.info(`   💰 IODEX: $${content.metadata.iodexPrice}`);
                }

                if (content.metadata.companies?.length > 0) {
                    pageLog.info(`   🏢 Empresas: ${content.metadata.companies.join(', ')}`);
                }
            } else {
                pageLog.warning(`   ⚠️ Falha na extração: ${content?.error || 'conteúdo vazio'}`);
            }

            // Pequeno delay antes do próximo artigo
            await page.waitForTimeout(2000);
        }

        return articles;

    } catch (error) {
        pageLog.error(`❌ Erro na extração: ${error.message}`);
        pageLog.error(`Stack: ${error.stack}`);
        return [];
    }
}

// ========== MAIN ==========

const input = await Actor.getInput() ?? {};
const {
    username,
    password,
    targetDate = null,
    dateFormat = 'BR',
    maxArticles = 2
} = input;

if (!username || !password) {
    log.error('Credenciais necessárias!');
    await Actor.pushData({
        type: 'error',
        message: 'Username e password são obrigatórios',
        timestamp: new Date().toISOString()
    });
    await Actor.exit();
}

const dateInfo = parseDateInput(targetDate, dateFormat);

log.info('========================================');
log.info('Platts IODEX Scraper v15.0 (ReadingPane)');
log.info('========================================');
log.info(`Username: ${username.substring(0, 3)}***`);
log.info(`Data input: ${targetDate || '(hoje)'}`);
log.info(`Data BR: ${dateInfo.br} | Data US: ${dateInfo.us}`);
log.info(`Máximo de artigos: ${maxArticles}`);
log.info('========================================');

const crawler = new PlaywrightCrawler({
    launchContext: {
        launchOptions: {
            headless: true,
            args: ['--no-sandbox', '--disable-setuid-sandbox']
        },
        useChrome: true
    },
    maxRequestRetries: 0,
    requestHandlerTimeoutSecs: 600,

    async requestHandler({ page, log: pageLog }) {
        try {
            // ETAPA 1: Login
            pageLog.info('🔐 Iniciando login...');
            const loginOk = await loginPlatts(page, username, password, pageLog);
            if (!loginOk) throw new Error('Login falhou');
            pageLog.info('✅ Login OK');

            // ETAPA 2: Raw Materials
            pageLog.info('🧭 Navegando para Raw Materials...');
            const rawOk = await navigateToRawMaterials(page, pageLog);
            if (!rawOk) throw new Error('Navegação falhou');
            pageLog.info('✅ Raw Materials OK');

            // ETAPA 3: Extrair notícias
            const articles = await extractSpecificDateNews(page, pageLog, dateInfo, maxArticles);

            // ETAPA 4: Output
            const result = {
                type: 'success',
                source: 'Platts IODEX Commentary and Rationale',
                version: '15.0',
                dateInput: {
                    original: targetDate || '(hoje)',
                    formatBR: dateInfo.br,
                    formatUS: dateInfo.us,
                    inputFormat: dateFormat
                },
                summary: {
                    requestedArticles: maxArticles,
                    foundArticles: articles.length,
                    totalWords: articles.reduce((sum, a) => sum + (a.metadata?.wordCount || 0), 0),
                    totalParagraphs: articles.reduce((sum, a) => sum + (a.metadata?.paragraphCount || 0), 0),
                    companiesMentioned: [...new Set(articles.flatMap(a => a.metadata?.companies || []))],
                    pricesFound: [...new Set(articles.flatMap(a => a.metadata?.prices || []))],
                    iodexPrices: articles.map(a => a.metadata?.iodexPrice).filter(Boolean)
                },
                articles,
                extractedAt: new Date().toISOString()
            };

            await Actor.pushData(result);

            // Resumo nos logs
            log.info('');
            log.info('========================================');
            log.info('📊 RESUMO v15.0');
            log.info('========================================');

            if (articles.length > 0) {
                log.info(`✅ ${articles.length} notícias extraídas`);
                log.info(`📅 Data: ${dateInfo.br} (BR) | ${dateInfo.us} (US)`);

                articles.forEach((article, idx) => {
                    log.info('');
                    log.info(`📰 Notícia ${idx + 1}:`);
                    log.info(`   Título: ${article.title?.substring(0, 80) || 'Sem título'}`);
                    log.info(`   Data: ${article.gridDateTime}`);
                    log.info(`   Autores: ${article.author || 'N/A'}`);
                    log.info(`   Palavras: ${article.metadata?.wordCount || 0} | Parágrafos: ${article.metadata?.paragraphCount || 0}`);

                    if (article.metadata?.iodexPrice) {
                        log.info(`   💰 IODEX: $${article.metadata.iodexPrice}/dmt`);
                    }

                    if (article.metadata?.lumpPremium) {
                        log.info(`   💰 Lump Premium: ${article.metadata.lumpPremium}`);
                    }

                    if (article.metadata?.companies?.length > 0) {
                        log.info(`   🏢 Empresas: ${article.metadata.companies.join(', ')}`);
                    }

                    if (article.metadata?.prices?.length > 0) {
                        log.info(`   💲 Preços USD: ${article.metadata.prices.slice(0, 5).join(', ')}`);
                    }
                });
            } else {
                log.warning(`⚠️ Nenhuma notícia encontrada para ${dateInfo.br} / ${dateInfo.us}`);
            }

            log.info('========================================');

        } catch (error) {
            pageLog.error(`❌ Erro fatal: ${error.message}`);
            await Actor.pushData({
                type: 'error',
                version: '15.0',
                error: error.message,
                stack: error.stack,
                timestamp: new Date().toISOString()
            });
            throw error;
        }
    }
});

await crawler.run(['about:blank']);

log.info('🏁 Scraper v15.0 finalizado');
await Actor.exit();