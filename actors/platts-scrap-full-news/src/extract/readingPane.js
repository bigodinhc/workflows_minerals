/**
 * Extrai conteúdo do `.readingpane-details` (padrão B — Raw Materials Workspace).
 *
 * Porting da lógica do `platts-news-only` v15.0:
 * - Título, data, autores
 * - Highlights (bullets curtos no topo)
 * - Corpo (parágrafos > 10 chars, filtra Cookie/Privacy/Terms)
 * - Metadata: wordCount, paragraphCount, prices, yuanPrices, percentages, companies,
 *   iodexPrice, iopexAssessments, lumpPremium
 */

export async function extractReadingPaneContent(page, paneIndex = -1) {
    return page.evaluate((idx) => {
        const allPanes = [...document.querySelectorAll('.readingpane-details')];
        let pane = null;
        if (idx >= 0 && idx < allPanes.length) {
            pane = allPanes[idx];
        } else {
            // Fallback: primeiro pane visível não vazio
            pane = allPanes.find((p) => p.offsetParent !== null && (p.innerText || '').length > 50) ||
                allPanes.find((p) => p.offsetParent !== null) ||
                allPanes[0];
        }
        if (!pane) return null;

        const data = {
            title: '',
            publishDate: '',
            author: '',
            authors: [],
            highlights: [],
            paragraphs: [],
            fullText: '',
            metadata: {},
        };

        // Título
        const h1 = pane.querySelector('h1, h2, .headline, [class*="title"]');
        if (h1) data.title = h1.innerText?.trim() || '';

        // Data (varrer elementos por padrão DD/MM/YYYY HH:MM:SS UTC)
        const allText = pane.innerText || '';
        const dateMatch = allText.match(/\d{2}\/\d{2}\/\d{4}\s+\d{2}:\d{2}:\d{2}\s+UTC/);
        if (dateMatch) data.publishDate = dateMatch[0];

        // Autores (links de email)
        const authorLinks = pane.querySelectorAll('a.auther-data-email-link, a[href^="mailto:"]');
        authorLinks.forEach((link) => {
            const name = link.innerText?.trim();
            if (name && name.length > 1 && name !== '|') data.authors.push(name);
        });
        data.author = data.authors.join(', ');

        // Parágrafos
        const allParagraphs = pane.querySelectorAll('p');
        const bodyParagraphs = [];
        const highlights = [];

        allParagraphs.forEach((p) => {
            const text = p.innerText?.trim();
            if (!text || text.length < 10) return;
            if (text.includes('Cookie') || text.includes('Privacy') || text.includes('Terms of Service')) return;

            // Highlights = parágrafos curtos antes do primeiro parágrafo longo
            if (bodyParagraphs.length === 0 && text.length < 80 && !text.includes('.')) {
                highlights.push(text);
            } else {
                bodyParagraphs.push(text);
            }
        });

        data.highlights = highlights;
        data.paragraphs = bodyParagraphs;
        data.fullText = bodyParagraphs.join('\n\n');

        // Metadata
        data.metadata.wordCount = data.fullText.split(/\s+/).filter((w) => w).length;
        data.metadata.paragraphCount = bodyParagraphs.length;
        data.metadata.highlightCount = highlights.length;

        // Preços
        const priceMatches = data.fullText.match(/\$[\d,]+\.?\d*/g);
        if (priceMatches) data.metadata.prices = [...new Set(priceMatches)];

        const yuanMatches = data.fullText.match(/Yuan\s*[\d,]+\.?\d*/gi);
        if (yuanMatches) data.metadata.yuanPrices = [...new Set(yuanMatches)];

        const percentMatches = data.fullText.match(/[\d]+\.?\d*%/g);
        if (percentMatches) data.metadata.percentages = [...new Set(percentMatches)];

        // Empresas
        const companies = [
            'Vale', 'Rio Tinto', 'BHP', 'FMG', 'Fortescue',
            'Cargill', 'Trafigura', 'Anglo American', 'CSN',
            'ArcelorMittal', 'Baosteel', 'POSCO', 'CMRG',
        ];
        data.metadata.companies = companies.filter((c) =>
            data.fullText.toLowerCase().includes(c.toLowerCase()),
        );

        // IODEX
        const iodexMatch = data.fullText.match(/IODEX at \$([\d.]+)/);
        if (iodexMatch) data.metadata.iodexPrice = iodexMatch[1];

        // IOPEX
        const iopexMatches = data.fullText.match(/IOPEX[^.]+/g);
        if (iopexMatches) data.metadata.iopexAssessments = iopexMatches;

        // Lump premium
        const lumpMatch = data.fullText.match(/lump premium at ([\d.]+ cents\/dmtu)/i);
        if (lumpMatch) data.metadata.lumpPremium = lumpMatch[1];

        return data;
    }, paneIndex);
}
