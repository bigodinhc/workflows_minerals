export async function closePopups(page) {
    try {
        await page.evaluate(() => {
            const selectors = ['.QSIWebResponsive', '.QSIPopOver', '[id*="QSI"]', '.modal-backdrop'];
            selectors.forEach((sel) => {
                document.querySelectorAll(sel).forEach((el) => {
                    el.style.display = 'none';
                    el.remove();
                });
            });
        });
    } catch (e) { /* silencioso */ }
}

const LOGIN_URL = 'https://www.spglobal.com/bin/commodityinsights/login';
const HOME_URL = 'https://www.spglobal.com/commodityinsights/en';

/**
 * Retorna { ok: true } em sucesso,
 * ou { ok: false, reason: 'auth-rejected' | 'navigation-failed' | 'timeout' | 'unknown', error }.
 * Quando reason === 'auth-rejected' o caller deve chamar session.markBad().
 */
export async function loginPlatts(page, username, password, pageLog) {
    try {
        pageLog.info('Navegando para página inicial...');
        await page.goto(HOME_URL, { waitUntil: 'domcontentloaded', timeout: 30000 });
        await closePopups(page);

        pageLog.info('Navegando para login...');
        await page.goto(LOGIN_URL, { waitUntil: 'networkidle', timeout: 30000 }).catch(() => { /* networkidle pode estourar, ok */ });

        // Pode redirecionar se sessão ainda válida
        if (page.url().includes('commodity-insights')) {
            await page.goto(LOGIN_URL, { waitUntil: 'networkidle', timeout: 30000 }).catch(() => {});
        }

        pageLog.info('Aguardando campo username...');
        try {
            await page.waitForSelector('input[name="identifier"]', { timeout: 20000, state: 'visible' });
        } catch (e) {
            return { ok: false, reason: 'timeout', error: `username field not found: ${e.message}` };
        }

        await page.click('input[name="identifier"]');
        await page.waitForTimeout(500);
        await page.fill('input[name="identifier"]', '');
        await page.type('input[name="identifier"]', username, { delay: 100 });

        pageLog.info('Clicando em Next...');
        const nextBtn = await page.$('input[type="submit"][value="Next"]') || await page.$('input[type="submit"]');
        if (nextBtn) await nextBtn.click();
        else await page.keyboard.press('Enter');

        // Aguarda próxima tela: ou método de senha (multi-factor) ou campo de senha direto
        pageLog.info('Aguardando tela de senha...');
        try {
            await page.waitForSelector(
                'div[data-se="okta_password"], input[type="password"], input[name="credentials.passcode"], .okta-form-infobox-error',
                { timeout: 15000 },
            );
        } catch (e) {
            return { ok: false, reason: 'timeout', error: `password screen not loaded: ${e.message}` };
        }

        // Usuário inexistente também mostra erro aqui
        if (await page.$('.okta-form-infobox-error')) {
            const errText = await page.$eval('.okta-form-infobox-error', (el) => el.innerText.trim()).catch(() => 'invalid user');
            return { ok: false, reason: 'auth-rejected', error: `user rejected: ${errText}` };
        }

        // Se apresentou seleção de método, escolhe senha
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
            // Okta precisa de ~2s pra renderizar próximo passo — único waitForTimeout genuíno
            await page.waitForTimeout(2000);
            await page.waitForSelector('input[type="password"], input[name="credentials.passcode"]', { timeout: 15000 });
        }

        pageLog.info('Preenchendo senha...');
        const passField = await page.$('input[name="credentials.passcode"]') || await page.$('input[type="password"]');
        await passField.click();
        await passField.fill(password);

        const verifyBtn = await page.$('input[type="submit"][value="Verify"]') || await page.$('input[type="submit"]');
        if (verifyBtn) await verifyBtn.click();
        else await page.keyboard.press('Enter');

        pageLog.info('Aguardando autenticação...');
        // Espera: ou URL fora de /login (sucesso) ou erro visível (rejeição)
        try {
            await Promise.race([
                page.waitForURL(/spglobal\.com\/(commodity-insights|commodityinsights)/, { timeout: 30000 }),
                page.waitForSelector('.okta-form-infobox-error', { timeout: 30000 }),
            ]);
        } catch (e) {
            return { ok: false, reason: 'timeout', error: `post-verify wait timed out: ${e.message}` };
        }

        // Erro visível = senha errada
        if (await page.$('.okta-form-infobox-error')) {
            const errText = await page.$eval('.okta-form-infobox-error', (el) => el.innerText.trim()).catch(() => 'invalid credentials');
            return { ok: false, reason: 'auth-rejected', error: errText };
        }

        // URL ainda em /login após janela de grace = falhou
        if (page.url().includes('/login') || page.url().includes('/oauth2/')) {
            return { ok: false, reason: 'auth-rejected', error: 'ainda em /login após Verify' };
        }

        await page.waitForLoadState('networkidle', { timeout: 20000 }).catch(() => { /* ok, pode ser SPA */ });
        pageLog.info(`Login OK. URL: ${page.url()}`);
        return { ok: true };
    } catch (error) {
        pageLog.error(`Erro login: ${error.message}`);
        return { ok: false, reason: 'unknown', error: error.message };
    }
}
