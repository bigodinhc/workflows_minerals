# Backfill dos Market Reports de 2025

Data: 2026-08-28
Actor: `actors/platts-scrap-reports`

## Objetivo

Baixar e armazenar as edições de 2025 dos Market Reports do Platts — cerca de
930 PDFs — reaproveitando o bucket, a tabela e o dedup do fluxo diário.

É um trabalho de uma vez, disparado à mão pelo console do Apify. Não entra em
cron.

## Descoberta que define o desenho

O botão "Archive" do grid não abre outra tela raspável: ele dispara uma API
JSON. Isso remove o DOM do caminho crítico.

```
POST api.platts.com/platts-platform/content-bff/v4/search/blendedsearch
GET  api.platts.com/platts-platform/content-bff/v2/search/stream/{Id}
```

Autenticação em ambas: `authorization: Bearer <JWT>` mais `appkey: realtime`.
O JWT é do SPA — não é cookie, então precisa ser capturado.

### Resposta do search

```jsonc
{
  "Items": [{
    "ReportName": "Steel Price Report",
    "CoverDate": "2026-08-27T00:00:00.000Z",
    "Frequency": "Daily",
    "Language": "English",
    "MimeType": "application/pdf",
    "PublicationGrouping": [
      { "Id": "8174a051-…", "MimeType": "application/pdf", "Name": "SPR_20260827.pdf" },
      { "Id": "0312a345-…", "MimeType": "application/xml", "Name": "SPR_20260827_TopicMerge.xml" }
    ]
  }],
  "TotalPages": 5, "TotalRecordCount": 249, "Page": 1, "PageSize": 50
}
```

Três consequências:

- **`CoverDate` é ISO 8601.** Some a ambiguidade DD/MM vs MM/DD que já quebrou o
  scraper de notícias e que o fluxo diário só evita por sorte do formato.
- **`TotalPages` e `TotalRecordCount` são explícitos.** A terminação da paginação
  é exata, não heurística. Um relatório com exatamente 50 edições numa página
  encerraria cedo um laço do tipo "veio menos que pageSize", e ninguém notaria.
- **`PublicationGrouping` tem PDF e XML.** Selecionar por
  `MimeType === 'application/pdf'`, nunca por índice.

## Volume

O grid lista 12 Market Reports, mas o `applyExcludeFilter` corta as versões em
português e espanhol. Os logs do actor confirmam: `Market Reports: 12 total,
8 after filter`. São **8 publicações**.

`Steel Price Report` retornou `TotalRecordCount: 249` numa janela de um ano.

| Frequência | Publicações | Edições/ano | Total |
|---|---|---|---|
| Daily | 3 | ~249 | ~747 |
| Weekly | 3 | ~52 | ~156 |
| Monthly | 2 | ~12 | ~24 |
| | **8** | | **~927** |

A ~650 KB por PDF, cerca de 600 MB transferidos. A ordem de grandeza importa
mais que o número: é um trabalho de dezenas de minutos, não de horas.

## Arquitetura

Novo modo no actor existente, atrás de `mode: "backfill"`. O caminho diário usa
`navigateGrid` / `extractRows` / `capturePdf`; o backfill não encosta em nenhum
dos três. O que compartilham são módulos estáveis: login, persistência, dedup,
utilitários de slug e o `EventBus`.

```
loginPlatts(page)                                    reuso
  ↓
captureAuth: page.on('request') → authorization + appkey
  ↓
navigateGrid('Market Reports') + extractRows          reuso — descobre as publicações
applyExcludeFilter                                    reuso — corta pt/es
  ↓
para cada publicação:
  ├─ POST blendedsearch { publication, fromDate, toDate, page }
  │    └─ pagina de 1 até TotalPages
  ├─ parseItems → { id, reportName, dateKey, frequency, name }
  ├─ isAlreadyStored(slug, dateKey)?  → pula          reuso
  ├─ GET stream/{id} → Buffer
  ├─ describeFileFormat(buf) !== 'PDF' → registra     reuso
  └─ uploadPdf(buf, storagePath)                      reuso
  ↓
uma mensagem de resumo no Telegram
```

O grid diz *o que* existe; a API diz *o histórico de cada um*. Se o Platts
adicionar um relatório, o backfill acompanha sem lista hardcoded.

### Módulos

| Arquivo | Responsabilidade | ~linhas |
|---|---|---|
| `src/api/plattsApi.js` | monta payload, executa search e stream, retenta | 100 |
| `src/api/captureAuth.js` | captura e recaptura os headers | 60 |
| `src/api/parseItems.js` | `Items[]` → linhas normalizadas | 60 |
| `src/backfill/runBackfill.js` | laço, dedup, upload, sumário | 150 |
| `src/main.js` | branch de `mode` | +5 |

As chamadas HTTP saem do Node via `ctx.request`, não de dentro da página.
`page.evaluate(fetch)` teria que trazer 650 KB de binário como base64 mil vezes;
`ctx.request.get()` devolve `Buffer` direto para o `uploadPdf`.

## Retomada

**O dedup do Supabase é o checkpoint.** Não há key-value store, arquivo de
estado, resume token nem tabela de progresso.

`isAlreadyStored(slug, dateKey)` já existe e já roda no fluxo diário. Uma
execução interrompida no PDF 800 retoma sozinha: o re-run re-lista tudo — 8
publicações × até 5 páginas, cerca de 40 chamadas — e pula o que já subiu.

O custo de reiniciar é re-listar, e re-listar é barato. Um subsistema de
checkpoint seria peso morto.

## Erros

| Situação | Reação |
|---|---|
| 401 / 403 | recaptura o token, retenta uma vez, depois lança |
| 429 / 5xx | backoff exponencial, 3 tentativas |
| não é PDF | registra com o formato nomeado, segue |
| falha ao listar uma publicação | registra, segue para a próxima |
| falha em item isolado | registra, `summary.type = 'partial'` |
| `TotalRecordCount > 0` e 0 itens processados | **lança** |

A última linha é deliberada. É o análogo do guard que o PR #11 adicionou ao
`extractRows` depois de quatro semanas de run verde entregando zero. Se a API
responder e nada for extraído, o run fica vermelho.

Renovação de token por reação, não por relógio: trata-se `401` como sinal para
recapturar. Não medimos o `exp` do JWT, e depender de um palpite de validade
traz o modo de falha de "o relógio dizia que faltava tempo, mas o servidor
discordou".

Concorrência 3 com backoff. Não há pressa que justifique irritar o Platts.

## Testes

Fixture: uma resposta real do `blendedsearch`, reduzida a três itens mais os
campos de paginação.

| Alvo | O que trava |
|---|---|
| `buildSearchPayload` | forma do payload conferida contra o que a UI envia |
| `parseItems` | escolhe o `application/pdf`, não o `_TopicMerge.xml` |
| `parseItems` | `CoverDate` → `dateKey`; item sem PDF no grouping é pulado |
| paginação | usa `TotalPages`; página curta no meio não encerra o laço |
| auth | 401 → recaptura → retenta; 401 de novo → lança |
| guard | `TotalRecordCount > 0` com 0 processados → lança |
| `runBackfill` | pula já-armazenados, segue após falha, acumula sumário |

### Fuso horário

`CoverDate` é meia-noite UTC. Em UTC-3,
`new Date("2026-08-27T00:00:00.000Z").getDate()` devolve **26** — mil registros
gravados um dia atrás, silenciosamente.

A conversão será `slice(0, 10)` sobre a string, sem passar por `Date`, com teste
explícito. Mesma família da armadilha MM/DD documentada em
`platts_headless_date_mmdd`.

## Input

```jsonc
{
  "mode": "backfill",              // ausente ou "daily" = comportamento atual
  "backfillFrom": "2025-01-01",
  "backfillTo":   "2025-12-31",
  "backfillPublications": [],      // vazio = as do grid, menos os excludes
  "backfillConcurrency": 3
}
```

Disparo pelo console do Apify com input customizado. Sem workflow novo no
GitHub, sem tocar no wrapper Python: o actor já tem `SUPABASE_URL` e
`SUPABASE_SERVICE_ROLE_KEY` nas próprias env vars — provado por comportamento,
o run de 28/08 subiu 28 PDFs sem que essas chaves passassem pelo input.

## Telegram

Uma mensagem ao final: total baixado, pulados por dedup, falhas por categoria,
duração. Sem botão por relatório — mil botões inline estouram o limite do
Telegram e seriam inúteis.

## Fora de escopo

- Research Reports. Metade é XLSX e PPTX, que o guard `%PDF` rejeita; incluí-los
  exige decidir o que fazer com formatos não-PDF, que é outra conversa.
- Anos anteriores a 2025. O desenho aceita qualquer janela por input, mas o
  volume e o tempo de execução só foram calibrados para um ano.
- Agendamento. É trabalho de uma vez.
