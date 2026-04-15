# Platts Reports PDF Downloader — Design

**Date:** 2026-04-15
**Status:** Approved by user, pending implementation plan

## Problem

Hoje o projeto coleta **notícias** de Platts via `actors/platts-scrap-full-news` (texto JSON pra Redis). Mas o portal SPGCI também publica **relatórios PDF** periódicos (Market Reports + Research Reports) que assinantes precisam consultar e distribuir. Não temos hoje nenhuma forma automatizada de baixar e arquivar esses PDFs.

User quer:

1. Baixar automaticamente todos os PDFs novos publicados nas duas grids do portal (`reportType=Market Reports` e `reportType=Research Reports`)
2. Arquivar como artefato permanente em Google Drive (organizado por tipo/ano/mês)
3. Distribuir cada PDF novo direto pro Telegram (mesmo chat das news), sem curadoria — entrega direta como `sendDocument`
4. Ignorar versões traduzidas duplicadas (Português/Espanhol) quando o original em inglês existe

## Scope

### In scope

- Novo actor `actors/platts-scrap-reports/` (duplicação do `platts-scrap-full-news` no que se refere a `auth/login.js`; resto é novo)
- Login Okta reaproveitando o flow já estável em produção (`core.spglobal.com`)
- Navegação nas 2 grids: `core.spglobal.com/#platts/rptsSearch?reportType=Market%20Reports` e `?reportType=Research%20Reports`
- Extração de metadata da tabela (Report Name, Frequency, Cover Date, Published Date)
- Filtragem heurística de duplicatas em outros idiomas (configurável)
- Captura do PDF via `page.waitForEvent('download')` clicando no ícone PDF da coluna Actions
- Upload pra Google Drive em `Platts Reports/<reportType>/<YYYY>/<MM>/<filename>.pdf` via service account já existente
- Dedup via Redis (`platts:report:seen:<slug>:<published-date>`, TTL 90d)
- Envio pra Telegram via `sendDocument` com legenda formatada
- GitHub Actions workflow novo (`platts_reports.yml`) rodando 1x/dia
- Wrapper Python (`execution/scripts/platts_reports.py`) que dispara o actor via `ApifyClient`

### Out of scope

- Extração de dados do PDF (sem OCR/parsing de tabelas/preços)
- Curadoria humana antes de distribuir (entrega é automática)
- Dashboard UI pra navegar PDFs (consulta direta no Drive)
- Persistência de metadata em Postgres/Supabase (Redis basta pra dedup; Drive é a fonte de verdade dos PDFs)
- Notificação de falha por canal separado (logs do GH Actions + run state em Redis bastam, igual aos outros workflows)
- Re-distribuição de relatórios antigos (apenas o que aparecer como "novo" segundo Redis dedup)

## Architecture

### Components

```
actors/platts-scrap-reports/
├── .actor/
│   ├── input_schema.json
│   └── actor.json
├── src/
│   ├── main.js                    # orquestra: login → loop reportTypes → loop rows → upload+notify
│   ├── auth/
│   │   └── login.js               # cópia do news actor (Okta flow estável)
│   ├── grid/
│   │   ├── navigateGrid.js        # navega URL, espera tabela renderizar
│   │   └── extractRows.js         # parseia DOM da tabela → array de metadata
│   ├── filters/
│   │   ├── translationFilter.js   # heurística de duplicata por idioma
│   │   └── customExclude.js       # blacklist substring do input
│   ├── download/
│   │   └── capturePdf.js          # click ícone Actions → waitForEvent('download') → buffer
│   ├── storage/
│   │   ├── gdriveUpload.js        # service account → cria subpastas se não existem → upload
│   │   └── redisDedup.js          # SET/GET de platts:report:seen:<slug>:<date>
│   ├── notify/
│   │   └── telegramSend.js        # sendDocument com legenda + chat_id
│   └── util/
│       ├── slug.js                # "SBB Steel Markets Daily" → "sbb-steel-markets-daily"
│       └── dates.js               # parse "15 abr. 2026" / "15/04/2026 10:24:16 UTC" → YYYY-MM-DD
```

```
execution/scripts/
└── platts_reports.py              # wrapper: lê env, chama ApifyClient.run_actor, registra state

.github/workflows/
└── platts_reports.yml             # cron diário, dispara python -m execution.scripts.platts_reports
```

### Input schema

```json
{
  "username":            "string (secret, required) — Platts SSO username",
  "password":            "string (secret, required) — Platts SSO password",
  "reportTypes":         ["Market Reports", "Research Reports"],  // default
  "excludeReportNames":  [
    "- Portuguese",
    "(Português)",
    "(Portugues)",
    "(Español)",
    "Perspectiva Global",
    "Panorama Semanal"
  ],                                                              // substring case-insensitive
  "maxReportsPerType":   50,                                      // upper bound de segurança
  "dryRun":              false,                                   // se true: extrai/filtra mas não baixa/upload/notifica
  "forceRedownload":     false,                                   // ignora dedup, baixa tudo de novo
  "gdriveFolderId":      "1KxixMP9rKF0vGzINGvmmyFvouaOvL02y",     // raiz "Platts Reports" no Drive
  "telegramChatId":      "<env>",                                 // default lê de env TELEGRAM_CHAT_ID
  "concurrency":         1                                        // PDFs sequencial (Akamai-friendly)
}
```

### Data flow

```
1. Login (auth/login.js) → BrowserContext autenticado
   - Mesma URL: https://core.spglobal.com/web/index1.html#login
   - Mesmo waitForFunction de saída do #login

2. FOR each reportType in input.reportTypes:
   a. navigateGrid(page, reportType)
      - GET https://core.spglobal.com/#platts/rptsSearch?reportType=<URL-encoded>
      - waitForSelector na tabela (tbd selector da grid após inspeção live)
   b. rows = extractRows(page)
      - Para cada linha: { reportName, reportTitle, frequency, coverDate, publishedDate, downloadHandle }
   c. filtered = applyFilters(rows, input.excludeReportNames)
      - translationFilter: skip se nome contém qualquer string da lista
   d. Sliced = filtered.slice(0, input.maxReportsPerType)
   e. FOR each row in sliced:
      - slug = slugify(row.reportName)
      - dateKey = parsePublishedDate(row.publishedDate)   // YYYY-MM-DD
      - seenKey = `platts:report:seen:${slug}:${dateKey}`
      - IF redis.exists(seenKey) AND !forceRedownload:
          summary.skipped.push({slug, dateKey, reason: "already-seen"})
          continue
      - filename = `${dateKey}_${slug}.pdf`
      - drivePath = `${reportType}/${YYYY}/${MM}/${filename}`
      - pdfBuffer = capturePdf(page, row.downloadHandle)   // click + waitForEvent('download')
      - IF dryRun:
          summary.would_download.push({slug, dateKey, drivePath, sizeBytes: pdfBuffer.length})
          continue
      - driveFileId = gdriveUpload(pdfBuffer, drivePath, input.gdriveFolderId)
      - telegramSend(pdfBuffer, filename, caption(row), chatId)
      - redis.setex(seenKey, 90*86400, "1")
      - summary.downloaded.push({slug, dateKey, driveFileId})

3. Apify dataset: push 1 wrapper item:
   {
     type: "success" | "partial" | "error",
     reportTypes: [...],
     downloaded: [...],
     skipped: [...],
     errors: [...],
     would_download: [...]   // só preenchido se dryRun
   }
```

### Slug, filename, Drive path

- **Slug:** lowercase, espaços → `-`, remove chars não-alfanuméricos exceto `-`. Ex: `"SBB Steel Markets Daily"` → `"sbb-steel-markets-daily"`. `"Global Market Outlook"` → `"global-market-outlook"`.
- **Filename:** `${YYYY-MM-DD}_${slug}.pdf` — Ex: `2026-04-14_sbb-steel-markets-daily.pdf`
- **Drive path completo:** `Platts Reports/Market Reports/2026/04/2026-04-14_sbb-steel-markets-daily.pdf`
- Subpastas (`Market Reports/`, `2026/`, `04/`) são criadas on-demand via `gdriveUpload`. Cache em-memória do mapeamento `path → folderId` por run pra evitar list calls repetidos.

### Translation filter (heurística)

Default exclui linhas onde `reportName.toLowerCase()` contém qualquer substring de:
- `"- portuguese"`, `"(português)"`, `"(portugues)"`, `"(español)"`
- `"perspectiva global"` (nome standalone do Global Market Outlook em espanhol)
- `"panorama semanal"` (nome standalone do weekly em português)

User pode adicionar/remover substrings via input. Match é puro substring case-insensitive — sem regex pra evitar erro de configuração. Stahl Global (alemão) **não** é excluído por default — não tem versão inglesa equivalente óbvia na mesma grid.

### Dedup strategy

- **Chave:** `platts:report:seen:<slug>:<YYYY-MM-DD>` (String "1")
- **TTL:** 90 dias (cobre quaisquer Monthly republicados; relatórios mais antigos que isso já estão arquivados no Drive de qualquer jeito)
- **Razão (Drive não basta):** Listar Drive a cada run pra checar existência é caro (1 API call por arquivo) e fica lento com o tempo. Redis é O(1).
- **Atomicidade:** Marca seen **só após** Drive upload + Telegram send bem-sucedidos. Se Telegram falhar, ainda marca seen (PDF tá salvo, repostagem é pior que silêncio).
- **Override:** `forceRedownload: true` no input ignora dedup.

### Error handling

| Cenário | Comportamento |
|---|---|
| Login falha (auth-rejected) | Aborta run inteiro, retorna `{type: "error", error: "auth-rejected"}` |
| Grid timeout numa categoria | Retry 1x com 5s backoff; depois pula esse reportType e segue o próximo (registra em `errors[]`) |
| Linha sem ícone PDF (relatório sem PDF disponível) | Skip silencioso, registra em `errors[]` com reason: "no-pdf-action" |
| Download timeout (>60s) | Loga, registra em `errors[]`, **não** marca seen, segue próximo |
| PDF baixado vazio (0 bytes) | Trata como erro, **não** marca seen, segue próximo |
| Drive upload falha | Loga, **não** marca seen nem envia Telegram, segue próximo |
| Telegram falha (4xx/5xx) | Loga em `errors[]`, **marca seen mesmo assim** (PDF tá no Drive, repostagem é ruim) |
| Redis indisponível | Aborta run — sem dedup é inseguro (re-baixaria/re-enviaria tudo todo dia) |

### State observation

Reusa `execution/core/state_store.py`. Workflow registrado como `"platts_reports"`.
- `wf:last_run:platts_reports` — última execução (status, summary)
- `wf:failures:platts_reports` — últimas 3 falhas
- `wf:streak:platts_reports` — falhas consecutivas (alerta ≥3)

### Schedule + deploy

**Workflow:** `.github/workflows/platts_reports.yml`
- Cron: `0 13 * * *` (10:00 BRT, todos os dias inclusive fim de semana — weekly/monthly podem aparecer em qualquer dia)
- Manual dispatch com `--dry-run` e `--force-redownload`

**Wrapper Python:** `execution/scripts/platts_reports.py`
- Lê env: `APIFY_API_TOKEN`, `PLATTS_USERNAME`, `PLATTS_PASSWORD`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `REDIS_URL`, `GDRIVE_PLATTS_REPORTS_FOLDER_ID`, `GOOGLE_CREDENTIALS_JSON`
- Chama `ApifyClient.run_actor("bigodeio05/platts-scrap-reports", run_input, memory_mbytes=4096, timeout_secs=900)`
- Pull dataset → loga summary → registra state

**Memória do actor:** 4GB (PDFs em buffer + Playwright). Bem abaixo do news actor (8GB).

### Telegram caption format

```
📊 *<Report Name>*
Cobertura: <coverDate>
Publicado: <publishedDate>
Frequência: <frequency>
```

Exemplo:
```
📊 *SBB Steel Markets Daily*
Cobertura: 14/04/2026
Publicado: 14/04/2026 21:51 UTC
Frequência: Daily
```

Markdown escapado (`_ * \` [`) nos campos dinâmicos pra evitar erro 400 no Bot API.

## New environment variables

```bash
# .env (já existe)
GDRIVE_PLATTS_REPORTS_FOLDER_ID=1KxixMP9rKF0vGzINGvmmyFvouaOvL02y
```

Resto reaproveita: `APIFY_API_TOKEN`, `PLATTS_USERNAME`, `PLATTS_PASSWORD`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `REDIS_URL`, `GOOGLE_CREDENTIALS_JSON`.

## Open items (resolver na fase de implementação live)

Esses dependem de inspeção ao vivo do portal logado — não dá pra resolver só com docs/screenshots:

1. **Selector exato da tabela** na página `rptsSearch` (provavelmente `table.report-grid` ou similar — confirmar inspecionando DOM)
2. **Selector do ícone PDF** na coluna Actions (3 ícones; PDF é o último — confirmar `aria-label` ou classe)
3. **Mecanismo do download:**
   - Caso A: Click dispara download direto (Playwright `waitForEvent('download')` captura) — caminho preferido
   - Caso B: Click abre nova aba/popup com viewer PDF → precisa interceptar via `context.on('response')` filtrando MIME `application/pdf`
   - Caso C: Click chama API JSON que retorna URL assinada → precisa um fetch extra
4. **Comportamento de paginação** (vejo 12 linhas em Market Reports — se houver mais que `maxReportsPerType` e tiver paginação, decidir se scrolla/clica "next")
5. **URL de paginação** na hash route (`#platts/rptsSearch?...&page=2` ou via interação JS)
6. **Tempo de carga inicial da grid** após login (definir timeout adequado, provavelmente 15-30s)

Esses 6 itens viram tarefas de "spike de exploração" no plano de implementação — modo `dryRun` do actor permite resolver iterativamente sem efeitos colaterais.

## Success criteria

1. Actor roda com sucesso em modo `dryRun`, lista todos os PDFs visíveis nas 2 grids (sem upload/notificação)
2. Actor roda em modo full e baixa pelo menos os relatórios diários do dia anterior pro Drive na estrutura `Market Reports/YYYY/MM/`
3. Telegram recebe `sendDocument` com legenda formatada pra cada PDF baixado
4. Re-execução no mesmo dia sem `forceRedownload` resulta em `skipped[]` com todos os PDFs já vistos, zero downloads novos
5. Falhas individuais (1 PDF quebrar) não impedem os outros de processarem
6. GH Actions workflow roda no schedule diário sem intervenção
