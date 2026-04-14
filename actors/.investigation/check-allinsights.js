// Rode no console de: https://core.spglobal.com/#platts/allInsights?keySector=Ferrous%20Metals
// Aguarde o carregamento completo antes. Cola o bloco INTEIRO.

(() => {
  const Q = (sel) => [...document.querySelectorAll(sel)];

  // FLASH banner
  const flashEls = Q('[class*="flash"], [class*="breaking"], [class*="alert"]')
    .filter((el) => {
      const t = el.innerText || '';
      return t.length > 0 && t.length < 300;
    })
    .slice(0, 5)
    .map((el) => ({
      tag: el.tagName,
      id: el.id || null,
      cls: (el.className || '').toString().slice(0, 100),
      text: (el.innerText || '').slice(0, 150),
    }));

  // Todos links de artigo
  const articleLinks = Q('a[href*="insightsArticle"], a[href*="articleID"]');

  // Parents únicos (caixas que contêm artigos)
  const parentContainers = {};
  articleLinks.forEach((a) => {
    const p = a.closest('[id]');
    if (!p) return;
    const id = p.id;
    if (!parentContainers[id]) parentContainers[id] = 0;
    parentContainers[id]++;
  });
  const parentList = Object.entries(parentContainers)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 15)
    .map(([id, count]) => ({ id, linkCount: count }));

  // IDs contendo palavras-chave
  const allIds = [...document.querySelectorAll('[id]')].map((e) => e.id);
  const matchingIds = allIds
    .filter((id) => /latest|flash|breaking|top-news|topNews|all-news|hot/i.test(id))
    .slice(0, 30);

  // Top News slider
  const slider = document.getElementById('platts-topNews-slider');
  const sliderLinks = slider ? slider.querySelectorAll('a[href*="insightsArticle"]').length : 0;

  // Amostra dos primeiros 3 links com seu parent
  const sample = articleLinks.slice(0, 5).map((a) => ({
    href: a.href,
    text: (a.innerText || '').slice(0, 80),
    parentId: a.closest('[id]')?.id || null,
    grandparentClass: a.parentElement?.parentElement?.className?.toString().slice(0, 100) || null,
  }));

  const result = {
    flashHits: flashEls,
    totalArticleLinks: articleLinks.length,
    sliderLinks,
    topParentContainers: parentList,
    matchingIds,
    sample,
  };

  console.log(JSON.stringify(result, null, 2));
  return result;
})();
