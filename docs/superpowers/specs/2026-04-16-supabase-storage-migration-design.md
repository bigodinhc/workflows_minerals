# Platts Reports: Drive → Supabase Storage Migration — Design

**Date:** 2026-04-16
**Status:** Approved by user, pending implementation plan

## Problem

O actor `platts-scrap-reports` usa Google Drive (OAuth2 refresh token) pra armazenar PDFs. Isso introduz:
- Complexidade de auth (OAuth2 + refresh token vs Service Account com quota zero)
- Dificuldade de consulta (listar pasta Drive vs query SQL)
- Sem metadata estruturada (pasta/nome de arquivo é tudo que temos)

Supabase Storage + Postgres resolve tudo: blob storage com metadata pesquisável, signed URLs pra distribuição, e o MCP/CLI facilita operação.

## Scope

### In scope

- Criar bucket `platts-reports` (privado) no Supabase project `antigravity-reports` (`liqiwvueesohlnnmezyw`, região `sa-east-1`)
- Criar tabela `platts_reports` com metadata (slug, date_key, report_name, report_type, frequency, cover_date, published_date, storage_path, file_size_bytes, telegram_message_id)
- UNIQUE constraint `(slug, date_key)` substitui Redis dedup — INSERT ON CONFLICT DO NOTHING
- Novo módulo `src/persist/supabaseUpload.js` substituindo `gdriveUpload.js` (mesmo contrato exportado: `uploadPdf`)
- Atualizar `src/main.js`: trocar import, remover Redis dedup calls, usar retorno do Supabase insert pra dedup
- Atualizar env vars no Apify actor: `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY`
- Remover `src/persist/gdriveUpload.js` e `src/persist/redisDedup.js`
- Atualizar `package.json`: adicionar `@supabase/supabase-js`, remover `googleapis` e `ioredis`

- Mudar distribuição Telegram: actor envia **1 mensagem resumo** com inline keyboard (1 botão por relatório) em vez de N documentos separados
- Novo callback handler no `webhook/app.py`: clique no botão → busca signed URL no Supabase → `sendDocument` 1 PDF sob demanda
- Atualizar `src/notify/telegramSend.js`: nova função `sendSummaryWithButtons` substituindo `sendPdfDocument` no fluxo principal

### Out of scope

- **Navegação completa no bot** (browse por ano/mês/dia, comando `/reports`) — feature futura, spec separada. Supabase já habilita via queries na tabela.
- Migração de PDFs já baixados do Drive pro Supabase (volume pequeno, pode fazer manual depois)
- RLS policies customizadas (service role key bypassa RLS; se precisar acesso público futuro, adiciona policy)
- Dashboard UI pra navegar PDFs

## Architecture

### Supabase project

- **Project:** `antigravity-reports` (`liqiwvueesohlnnmezyw`)
- **Region:** `sa-east-1` (São Paulo)
- **Database:** Postgres 17

### Tabela `platts_reports`

```sql
CREATE TABLE platts_reports (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    slug TEXT NOT NULL,
    date_key DATE NOT NULL,
    report_name TEXT NOT NULL,
    report_type TEXT NOT NULL,
    frequency TEXT,
    cover_date TEXT,
    published_date TEXT,
    storage_path TEXT NOT NULL,
    file_size_bytes BIGINT,
    telegram_message_id BIGINT,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(slug, date_key)
);
```

### Bucket `platts-reports`

- Acesso: **privado** (default RLS, sem policy pública)
- Signed URL com expiração 1h quando necessário (bot Telegram futuro)
- Path convention: `<report-type-slug>/<year>/<month>/<date_key>_<slug>.pdf`
  - Ex: `market-reports/2026/04/2026-04-15_steel-price-report.pdf`

### Módulo `supabaseUpload.js`

```
export async function uploadPdf(pdfBuffer, { storagePath, metadata })
  → { path, signedUrl } | throws on storage/db error
```

Internamente:
1. `supabase.storage.from('platts-reports').upload(storagePath, pdfBuffer, { contentType: 'application/pdf', upsert: false })`
2. `supabase.from('platts_reports').insert(metadata)` com `.select()` pra confirmar insert
3. Se constraint UNIQUE viola (já existe) → retorna `null` (caller trata como skip)

### Dedup strategy (substitui Redis)

- **Antes:** `platts:report:seen:<slug>:<date_key>` no Redis (TTL 90d)
- **Agora:** `INSERT INTO platts_reports ... ON CONFLICT (slug, date_key) DO NOTHING RETURNING id`
  - Se retorna row → novo, prossegue com upload
  - Se retorna vazio → duplicata, skip
- **Vantagem:** dedup é persistente (sem TTL), atômico (constraint DB), e pesquisável

### Fluxo atualizado no `main.js`

```
1. Login Platts
2. Para cada reportType:
   a. navigateGrid → extractRows → applyFilters
   b. Para cada row:
      - slugify + parseDate
      - Dedup: SELECT 1 FROM platts_reports WHERE slug AND date_key → skip se existe
      - Se novo: capturePdf → storage.upload → INSERT metadata
      - Se duplicata: summary.skipped.push(...)
3. Após o loop: sendSummaryMessage(chatId, summary.downloaded) com inline keyboard
4. Actor.pushData(summary)
```

Dedup check via SELECT antes do capturePdf (evita baixar PDF que já temos). INSERT após storage.upload (só registra sucesso). Telegram é 1 mensagem resumo no final, não por-relatório.

### Telegram: resumo + download on-demand

**Antes:** Actor mandava N `sendDocument` (1 PDF por relatório) → chat lotado de arquivos.

**Agora:** Actor manda **1 mensagem resumo** com inline keyboard. Usuário clica no botão do relatório que quer → webhook busca signed URL no Supabase → manda 1 PDF.

#### Mensagem resumo (actor envia via `telegramSend.js`)

```
📊 Platts Reports — 15/04/2026

8 relatórios novos:
• Steel Price Report (Daily)
• SBB Steel Markets Daily (Daily)
• World Steel Review (Weekly)
• Steel Business Briefing (Daily)
• Stahl Global (Weekly)
• Cement Weekly (Weekly)
• Global Market Outlook (Monthly)
• Steel Raw Materials Monthly (Monthly)
```

Inline keyboard: 1 botão por relatório, callback data = `report_dl:<report_uuid>`.
Se > 8 relatórios, paginar botões (Telegram suporta até 100 botões, 1-2 por linha).

#### Callback handler (webhook/app.py)

Novo handler para `report_dl:<uuid>`:

1. Busca registro na tabela `platts_reports` pelo `id` (UUID)
2. Gera signed URL (expiração 1h): `supabase.storage.from('platts-reports').create_signed_url(storage_path, 3600)`
3. Faz download do PDF via signed URL
4. Envia via `sendDocument` como resposta ao callback
5. Responde callback com `answerCallbackQuery`

**Dependência no webhook:** Precisa de `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` no Railway env vars (já existem REDIS_URL lá, adicionar essas 2).

#### Fluxo atualizado

```
Actor:
  capturePdf → storage.upload → INSERT metadata → acumula lista

  Após loop de todos os relatórios:
  → sendSummaryMessage(chatId, downloadedList) com inline keyboard

Webhook (on button click):
  report_dl:<uuid> → SELECT storage_path → signed URL → sendDocument
```

### Env vars

**Adicionar no Apify actor:**
- `SUPABASE_URL` — `https://liqiwvueesohlnnmezyw.supabase.co`
- `SUPABASE_SERVICE_ROLE_KEY` — service role key do projeto

**Remover do Apify actor:**
- `GOOGLE_OAUTH_CLIENT_ID`
- `GOOGLE_OAUTH_CLIENT_SECRET`
- `GOOGLE_OAUTH_REFRESH_TOKEN`
- `GOOGLE_CREDENTIALS_JSON`
- `REDIS_URL` (não é mais necessário no actor — outros workflows continuam usando)

**Remover do input schema:**
- `gdriveFolderId` (não existe mais)

**Adicionar no Railway (webhook):**
- `SUPABASE_URL` — mesma URL
- `SUPABASE_SERVICE_ROLE_KEY` — mesma key (webhook precisa pra gerar signed URLs no callback)

### Dependências npm

**Adicionar:** `@supabase/supabase-js`
**Remover:** `googleapis`, `ioredis`

### Error handling

| Cenário | Comportamento |
|---|---|
| Supabase storage upload falha | Log warning, registra em `errors[]`, skip row, continua |
| Supabase DB insert falha (não-UNIQUE) | Log warning, registra em `errors[]`, skip row |
| Dedup check (SELECT) retorna existente | `summary.skipped.push({reason: "already-exists"})`, skip |
| Telegram summary falha | Log warning, registra em `errors[]`, PDFs já salvos no Supabase (acessíveis via dashboard) |
| Webhook callback: UUID não encontrado | Responde "Relatório não encontrado" via answerCallbackQuery |
| Webhook callback: signed URL falha | Responde "Erro ao gerar link" via answerCallbackQuery |
| SUPABASE_URL ou KEY ausente | Actor.fail() imediato |

## Success criteria

1. Actor roda com dryRun=false, PDFs aparecem no bucket `platts-reports` com paths hierárquicos
2. Tabela `platts_reports` tem 1 registro por PDF baixado com metadata completa
3. Re-execução no mesmo dia → `skipped[]` com todos os já-existentes, zero duplicatas
4. Telegram recebe 1 mensagem resumo com inline keyboard (1 botão por relatório)
5. Clicar botão no Telegram → webhook busca signed URL → envia PDF como documento
6. `googleapis`, `ioredis` removidos do `package.json`
7. Env vars Google/Redis removidas do actor no Apify

## Pendências futuras (out of scope)

- **Navegação completa via bot:** comando `/reports` → browse por ano → mês → dia → relatório. Query na tabela `platts_reports` via Supabase. Spec separada.
