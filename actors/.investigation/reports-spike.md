# Reports Spike Findings — 2026-04-15

## 1. Grid table selector
- Selector: `<exact CSS or XPath>`
- Contains how many rows by default? <number>

## 2. Row + column selectors
- Row: `<selector>`
- Report Name cell: `<selector>` (text() or .innerText)
- Frequency cell: `<selector>`
- Cover Date cell: `<selector>`
- Published Date cell: `<selector>` (full timestamp visible)
- Actions cell: `<selector>`

## 3. PDF action icon selector
- Selector: `<selector>` (likely `[aria-label="Download PDF"]` or `.pdf-icon` or 3rd `<a>` in Actions)
- Behavior on click:
  - [ ] Triggers download event (best case → use `page.waitForEvent('download')`)
  - [ ] Opens new tab with PDF viewer (fallback → intercept response)
  - [ ] Calls API returning signed URL (fallback → fetch URL inside Playwright context)

## 4. Pagination
- Total reports per type (visible after scrolling/clicking next): <number>
- Mechanism: [ ] infinite scroll  [ ] page numbers  [ ] no pagination (all loaded)

## 5. Grid load time after navigation
- Approximate ms from navigation to first row visible: <ms>
- Selector that signals "ready": `<selector>` (e.g., `tbody tr:first-child`)

## 6. Research Reports — same structure?
- Visit https://core.spglobal.com/#platts/rptsSearch?reportType=Research%20Reports
- Same selectors work? [ ] yes  [ ] no — differences: <list>

## 7. Sample published date format observed
- Examples: `15/04/2026 10:24:16 UTC`, `<other formats?>`
