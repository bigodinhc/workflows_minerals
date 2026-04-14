import { Actor } from 'apify';

export async function extractArticleImages(page, pageLog) {
    try {
        pageLog.info('   🖼️ Extraindo imagens do artigo...');

        const imagesData = await page.evaluate(() => {
            const images = [];
            const articleEl = document.querySelector('.platts-newsSection-article') ||
                document.querySelector('article') ||
                document.querySelector('.newsSection-body');

            if (!articleEl) return images;

            const thumbnailImg = articleEl.querySelector('.platts-news-article-thumbnail img') ||
                document.querySelector('.platts-news-article-thumbnail img');

            if (thumbnailImg && thumbnailImg.src && thumbnailImg.src.length > 100) {
                let mimeType = 'image/jpeg';
                const mimeMatch = thumbnailImg.src.match(/data:([^;]+);/);
                if (mimeMatch) mimeType = mimeMatch[1];

                images.push({
                    type: 'thumbnail',
                    index: 0,
                    src: thumbnailImg.src,
                    width: thumbnailImg.naturalWidth || thumbnailImg.width || 0,
                    height: thumbnailImg.naturalHeight || thumbnailImg.height || 0,
                    alt: thumbnailImg.alt || '',
                    caption: '',
                    isBase64: thumbnailImg.src.startsWith('data:'),
                    mimeType,
                    sizeKB: Math.round(thumbnailImg.src.length * 0.75 / 1024),
                });
            }

            const bodyEl = articleEl.querySelector('.newsSection-body') || articleEl;
            const allImgs = bodyEl.querySelectorAll('img');

            let chartIndex = 0;
            allImgs.forEach((img) => {
                if (img.closest('.platts-news-article-thumbnail') ||
                    img.closest('.platts-inarticle-thumbnail-container') ||
                    img.alt === 'Thumbnail Image') {
                    return;
                }

                const src = img.src || '';
                if (!src || src.length < 500) return;

                let mimeType = 'image/png';
                const mimeMatch = src.match(/data:([^;]+);/);
                if (mimeMatch) mimeType = mimeMatch[1];

                let caption = '';
                const parent = img.parentElement;
                if (parent) {
                    let nextEl = parent.nextElementSibling;
                    while (nextEl && !caption) {
                        const text = nextEl.textContent?.trim() || '';
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
                    src,
                    width: img.naturalWidth || img.width || 0,
                    height: img.naturalHeight || img.height || 0,
                    alt: img.alt || '',
                    caption,
                    isBase64: src.startsWith('data:'),
                    mimeType,
                    sizeKB: Math.round(src.length * 0.75 / 1024),
                });
            });

            return images;
        });

        const thumbnailCount = imagesData.filter((i) => i.type === 'thumbnail').length;
        const chartCount = imagesData.filter((i) => i.type === 'chart').length;
        pageLog.info(`      📷 Thumbnail: ${thumbnailCount}`);
        pageLog.info(`      📈 Gráficos: ${chartCount}`);

        return imagesData;
    } catch (error) {
        pageLog.error(`   ❌ Erro extraindo imagens: ${error.message}`);
        return [];
    }
}

export async function saveImagesToStore(images, articleId, pageLog) {
    const savedImages = [];

    for (const img of images) {
        try {
            if (!img.src || !img.isBase64) continue;

            const base64Match = img.src.match(/data:([^;]+);base64,(.+)/);
            if (!base64Match) {
                pageLog.warning(`      ⚠️ Formato base64 inválido para imagem ${img.index}`);
                continue;
            }

            const mimeType = base64Match[1];
            const base64Data = base64Match[2];

            let extension = 'png';
            if (mimeType.includes('jpeg') || mimeType.includes('jpg')) extension = 'jpg';
            else if (mimeType.includes('svg')) extension = 'svg';
            else if (mimeType.includes('png')) extension = 'png';
            else if (mimeType.includes('gif')) extension = 'gif';
            else if (mimeType.includes('webp')) extension = 'webp';

            const safeArticleId = articleId.replace(/[^a-zA-Z0-9-_]/g, '_').substring(0, 50);
            const filename = `${safeArticleId}_${img.type}_${img.index}.${extension}`;
            const buffer = Buffer.from(base64Data, 'base64');

            await Actor.setValue(filename, buffer, { contentType: mimeType });

            savedImages.push({
                filename,
                type: img.type,
                index: img.index,
                width: img.width,
                height: img.height,
                caption: img.caption,
                mimeType,
                sizeKB: Math.round(buffer.length / 1024),
                storeKey: filename,
            });

            pageLog.info(`      ✅ Salvo: ${filename} (${Math.round(buffer.length / 1024)}KB)`);
        } catch (error) {
            pageLog.error(`      ❌ Erro salvando imagem ${img.type}_${img.index}: ${error.message}`);
        }
    }

    return savedImages;
}
