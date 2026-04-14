/**
 * Platts Iron Ore Scraper v28 - VERSÃO FINAL
 * Extração completa de 166 símbolos com descrições e dados
 */

import { PlaywrightCrawler, log } from 'crawlee';
import { Actor } from 'apify';

await Actor.init();

// ========== FUNÇÃO DE LOGIN (mantém a mesma testada) ==========
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
                            return true;
                        }
                    }
                    return false;
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
        
        const finalUrl = page.url();
        pageLog.info(`Login concluído. URL: ${finalUrl}`);
        
        return true;
        
    } catch (error) {
        pageLog.error(`Erro no login: ${error.message}`);
        return false;
    }
}

// ========== FUNÇÃO PARA NAVEGAR PARA IRON ORE ==========
async function navigateToIronOre(page, pageLog) {
    try {
        const currentUrl = page.url();
        pageLog.info(`URL atual: ${currentUrl}`);
        
        if (!currentUrl.includes('plattsconnect.spglobal.com')) {
            pageLog.info('Navegando para Platts Connect...');
            
            const plattsLinkClicked = await page.evaluate(() => {
                const links = document.querySelectorAll('a');
                for (const link of links) {
                    if (link.href && link.href.includes('plattsconnect.spglobal.com')) {
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
        
        await page.waitForTimeout(5000);
        
        pageLog.info('Navegando para Iron Ore Price Assessments...');
        const ironOreUrl = 'https://plattsconnect.spglobal.com/#platts/topic?menuserviceline=Ferrous%20Metals&serviceline=Steel%20%26%20Raw%20Materials&topic=Iron%20Ore';
        
        await page.goto(ironOreUrl, {
            waitUntil: 'domcontentloaded',
            timeout: 30000
        });
        
        pageLog.info('Aguardando tabela carregar...');
        await page.waitForTimeout(15000);
        
        const hasTable = await page.evaluate(() => {
            return document.querySelector('.ag-root-wrapper') !== null;
        });
        
        if (hasTable) {
            pageLog.info('Tabela AG-Grid detectada!');
            await page.waitForTimeout(5000);
            return true;
        } else {
            pageLog.warning('Tabela não encontrada, mas continuando...');
            return false;
        }
        
    } catch (error) {
        pageLog.error(`Erro na navegação: ${error.message}`);
        return false;
    }
}

// ========== NOVA FUNÇÃO DE EXTRAÇÃO COMPLETA ==========
async function extractCompleteIronOreData(page, pageLog) {
    pageLog.info('Iniciando extração completa combinando descrições e dados...');
    
    const extractedData = await page.evaluate(async () => {
        const fullDataMap = new Map();
        
        // Containers
        const leftContainer = document.querySelector('.ag-pinned-left-cols-container');
        const centerContainer = document.querySelector('.ag-center-cols-container');
        const viewport = document.querySelector('.ag-body-viewport');
        
        if (!leftContainer || !centerContainer || !viewport) {
            return {
                error: 'Containers não encontrados',
                data: []
            };
        }
        
        // Expandir container central para mostrar todas as colunas
        console.log('Expandindo container para mostrar todas as colunas...');
        const originalStyle = centerContainer.getAttribute('style');
        centerContainer.style.width = '5000px';
        await new Promise(r => setTimeout(r, 300));
        
        // Função para extrair dados visíveis
        function extractVisibleData() {
            const leftRows = leftContainer.querySelectorAll('.ag-row');
            const centerRows = centerContainer.querySelectorAll('.ag-row');
            
            const rowCount = Math.min(leftRows.length, centerRows.length);
            
            for (let i = 0; i < rowCount; i++) {
                const leftRow = leftRows[i];
                const centerRow = centerRows[i];
                
                // Extrair descrição e símbolo da coluna esquerda
                const descLink = leftRow.querySelector('a.description');
                if (descLink) {
                    const symbol = descLink.href?.split('symbol=')[1];
                    const description = descLink.innerText?.trim();
                    
                    if (symbol) {
                        // Extrair dados da coluna central
                        const cells = centerRow.querySelectorAll('.ag-cell');
                        
                        if (!fullDataMap.has(symbol)) {
                            fullDataMap.set(symbol, {
                                symbol: symbol,
                                description: description,
                                price: cells[1]?.innerText?.trim() || '',
                                chg: cells[2]?.innerText?.trim() || '',
                                chgPercent: cells[3]?.innerText?.trim() || '',
                                assessedDate: cells[4]?.innerText?.trim() || '',
                                currUom: cells[5]?.innerText?.trim() || '',
                                assessmentType: cells[6]?.innerText?.trim() || '',
                                subCommodity: cells[7]?.innerText?.trim() || ''
                            });
                        }
                    }
                }
            }
        }
        
        // Fazer scroll vertical completo
        viewport.scrollTop = 0;
        await new Promise(r => setTimeout(r, 300));
        
        const scrollHeight = viewport.scrollHeight;
        const clientHeight = viewport.clientHeight;
        const maxScroll = scrollHeight - clientHeight;
        
        console.log(`Altura total: ${scrollHeight}px, Scroll máximo: ${maxScroll}px`);
        
        // Scroll em passos pequenos
        for (let scrollPos = 0; scrollPos <= maxScroll; scrollPos += 50) {
            viewport.scrollTop = scrollPos;
            await new Promise(r => setTimeout(r, 100));
            
            extractVisibleData();
            
            if (scrollPos % 500 === 0 || scrollPos === maxScroll) {
                console.log(`Posição ${scrollPos}px: ${fullDataMap.size} símbolos coletados`);
            }
        }
        
        // Fazer scroll final para garantir
        viewport.scrollTop = maxScroll;
        await new Promise(r => setTimeout(r, 200));
        extractVisibleData();
        
        // Voltar ao topo
        viewport.scrollTop = 0;
        
        // Restaurar estilo original
        centerContainer.setAttribute('style', originalStyle);
        
        // Converter Map para Array
        const finalData = Array.from(fullDataMap.values());
        
        return {
            data: finalData,
            totalExtracted: finalData.length,
            withPrice: finalData.filter(d => d.price && d.price !== '').length,
            withDescription: finalData.filter(d => d.description && d.description !== '').length
        };
    });
    
    pageLog.info(`Extração concluída: ${extractedData.totalExtracted} símbolos`);
    pageLog.info(`  Com descrição: ${extractedData.withDescription}`);
    pageLog.info(`  Com preço: ${extractedData.withPrice}`);
    
    return extractedData;
}

// ========== CONFIGURAÇÃO PRINCIPAL ==========
const input = await Actor.getInput() ?? {};
const { username, password } = input;

if (!username || !password) {
    log.error('Credenciais necessárias!');
    await Actor.pushData({
        type: 'error',
        message: 'Username e password são obrigatórios',
        timestamp: new Date().toISOString()
    });
    await Actor.exit();
}

log.info('=' .repeat(70));
log.info('Platts Iron Ore Scraper v28 - VERSÃO FINAL');
log.info('=' .repeat(70));

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
            const loginOk = await loginPlatts(page, username, password, pageLog);
            if (!loginOk) {
                throw new Error('Login falhou');
            }
            
            pageLog.info('Login concluído com sucesso!');
            
            // ETAPA 2: Navegar para Iron Ore
            const navigationOk = await navigateToIronOre(page, pageLog);
            if (!navigationOk) {
                pageLog.warning('Navegação parcialmente bem-sucedida, continuando...');
            }
            
            // ETAPA 3: Extração completa
            const extractionResult = await extractCompleteIronOreData(page, pageLog);
            
            // ETAPA 4: Organizar dados por categoria
            const categorizedData = {
                'IO': [],
                'TSI': [],
                'AAQ': [],
                'SB': [],
                'CA': [],
                'SI': [],
                'AH': [],
                'APO': [],
                'TS': [],
                'Others': []
            };
            
            extractionResult.data.forEach(item => {
                const prefix = item.symbol.substring(0, 2);
                if (prefix === 'IO') categorizedData.IO.push(item);
                else if (item.symbol.startsWith('TSI')) categorizedData.TSI.push(item);
                else if (item.symbol.startsWith('AAQ')) categorizedData.AAQ.push(item);
                else if (prefix === 'SB') categorizedData.SB.push(item);
                else if (prefix === 'CA') categorizedData.CA.push(item);
                else if (prefix === 'SI') categorizedData.SI.push(item);
                else if (prefix === 'AH') categorizedData.AH.push(item);
                else if (prefix === 'AP') categorizedData.APO.push(item);
                else if (prefix === 'TS') categorizedData.TS.push(item);
                else categorizedData.Others.push(item);
            });
            
            // ETAPA 5: Resultado final
            const finalData = {
                type: 'success',
                source: 'Platts Iron Ore Price Assessments',
                summary: {
                    totalExtracted: extractionResult.totalExtracted,
                    withDescription: extractionResult.withDescription,
                    withPrice: extractionResult.withPrice,
                    breakdown: {
                        IO: categorizedData.IO.length,
                        TSI: categorizedData.TSI.length,
                        AAQ: categorizedData.AAQ.length,
                        SB: categorizedData.SB.length,
                        CA: categorizedData.CA.length,
                        SI: categorizedData.SI.length,
                        AH: categorizedData.AH.length,
                        APO: categorizedData.APO.length,
                        TS: categorizedData.TS.length,
                        Others: categorizedData.Others.length
                    },
                    extractedAt: new Date().toISOString()
                },
                data: extractionResult.data,
                categorized: categorizedData
            };
            
            await Actor.pushData(finalData);
            
            log.info('=' .repeat(70));
            log.info(`✅ EXTRAÇÃO COMPLETA: ${extractionResult.totalExtracted} símbolos`);
            log.info(`   IO: ${categorizedData.IO.length} símbolos`);
            log.info(`   TSI: ${categorizedData.TSI.length} símbolos`);
            log.info(`   AAQ: ${categorizedData.AAQ.length} símbolos`);
            log.info(`   Outros: ${categorizedData.Others.length} símbolos`);
            
            // Verificar símbolos importantes
            const importantSymbols = ['IOBBA00', 'IODBZ00', 'IOPRM00', 'IODFE00'];
            log.info('\n🎯 Símbolos importantes:');
            importantSymbols.forEach(sym => {
                const data = extractionResult.data.find(d => d.symbol === sym);
                if (data) {
                    log.info(`  ✅ ${sym}: ${data.price} | ${data.chg}`);
                }
            });
            
            log.info('=' .repeat(70));
            
        } catch (error) {
            pageLog.error(`Erro: ${error.message}`);
            await Actor.pushData({
                type: 'error',
                error: error.message,
                timestamp: new Date().toISOString()
            });
            throw error;
        }
    }
});

await crawler.run(['about:blank']);

log.info('Scraper finalizado com sucesso!');
await Actor.exit();