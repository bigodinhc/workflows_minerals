// Rode no console de: https://core.spglobal.com/#platts/workspace?workspace=Raw%20Materials%20Workspace&type=public
// Aguarde a tabela inferior carregar (a que tem IODEX Commentary and Rationale etc). Cola o bloco INTEIRO.

(() => {
  const Q = (sel) => [...document.querySelectorAll(sel)];

  // Tabs (pode ter várias estruturas)
  const tabCandidates = Q('[id*="widget-area-tab"], [role="tab"], [class*="tab-button"], [class*="TabButton"]')
    .filter((t) => t.offsetParent !== null)
    .slice(0, 20)
    .map((t) => ({
      tag: t.tagName,
      id: t.id || null,
      role: t.getAttribute('role') || null,
      cls: (t.className || '').toString().slice(0, 100),
      text: (t.innerText || '').slice(0, 60),
      ariaSelected: t.getAttribute('aria-selected') || null,
    }));

  // Grids do AG-Grid
  const grids = Q('.ag-root-wrapper').map((g, i) => ({
    i,
    visible: g.offsetParent !== null,
    rows: g.querySelectorAll('.ag-row').length,
    anchors: g.querySelectorAll('button.ag-anchor, a.ag-anchor, [role="link"]').length,
    hasDateColumn: /\d{2}\/\d{2}\/\d{4}/.test(g.innerText || ''),
    // Primeiros 3 textos de header se houver
    headers: [...(g.querySelectorAll('.ag-header-cell-text') || [])].slice(0, 8).map((h) => (h.innerText || '').trim()),
  }));

  // Reading pane
  const paneSelectors = ['.readingpane-details', '[class*="readingpane"]', '[class*="ReadingPane"]', '[class*="reading-pane"]'];
  let pane = null;
  let paneSelector = null;
  for (const s of paneSelectors) {
    pane = document.querySelector(s);
    if (pane) { paneSelector = s; break; }
  }

  // Headers de seção (indicam que tabelas existem: Rationale, Commentary, IODEX, Lump, BOFs)
  const sectionHeaders = Q('h2, h3, h4, [class*="title"], [class*="header"]')
    .filter((h) => {
      const t = (h.innerText || '').trim();
      return t.length > 0 && t.length < 150 && /Rationale|Commentary|IODEX|Lump|BOFs|Summary/i.test(t);
    })
    .slice(0, 15)
    .map((h) => ({
      tag: h.tagName,
      id: h.id || null,
      cls: (h.className || '').toString().slice(0, 100),
      text: (h.innerText || '').slice(0, 120),
    }));

  // Links clicáveis dentro de rows (abrem reading pane)
  const rowAnchors = Q('button.ag-anchor, a.ag-anchor, [role="link"]')
    .filter((a) => a.offsetParent !== null)
    .slice(0, 5)
    .map((a) => ({
      tag: a.tagName,
      role: a.getAttribute('role') || null,
      cls: (a.className || '').toString().slice(0, 80),
      text: (a.innerText || '').slice(0, 80),
      parentClosestId: a.closest('[id]')?.id || null,
    }));

  // IDs com "widget" (estrutura do Platts)
  const widgetIds = [...document.querySelectorAll('[id*="widget"]')]
    .slice(0, 20)
    .map((e) => ({ id: e.id, tag: e.tagName, visible: e.offsetParent !== null }));

  const result = {
    tabCandidates,
    grids,
    paneFound: !!pane,
    paneSelector,
    paneClass: pane ? (pane.className || '').toString().slice(0, 150) : null,
    sectionHeaders,
    rowAnchors,
    widgetIds,
  };

  console.log(JSON.stringify(result, null, 2));
  return result;
})();
