# Reports Bot Navigation via Telegram — Design

**Date:** 2026-04-16
**Status:** Approved by user, pending implementation plan

## Problem

Os relatórios Platts são baixados automaticamente pro Supabase Storage e o Telegram recebe 1 mensagem resumo com botões de download. Mas não há como consultar relatórios passados — o trader precisa abrir o Supabase dashboard pra encontrar um PDF antigo. Queremos navegação interativa no Telegram via comando `/reports`.

## Scope

### In scope

- Comando `/reports` no Telegram com navegação: tipo → últimos 10 → browse por ano/mês → download
- Callbacks inline no webhook: `rpt_type`, `rpt_years`, `rpt_month`, `rpt_back`
- Reusa `report_dl:<uuid>` (já existe) para entrega final do PDF
- Apenas admin (validação via `ADMIN_CHAT_ID`)
- Navegação por edição de mensagem (não envia novas — chat limpo)
- Registrar todos os comandos do bot via `setMyCommands` API

### Out of scope

- Busca por texto (ex: "acha o Steel Markets Daily de março")
- Navegação pra outros usuários que não admin
- Dashboard web de PDFs

## Architecture

### Entry point

Comando `/reports` no chat. Bot responde com menu de tipos (inline keyboard). Só admin pode usar.

### Navigation flow

```
/reports
  → [📊 Market Reports]  [📊 Research Reports]

  (clicou Market Reports)
  → Últimos relatórios:
    [Steel Price Report — 15/04/2026]
    [SBB Steel Markets Daily — 15/04/2026]
    [World Steel Review — 15/04/2026]
    ...até 10 itens
    [📅 Ver por data]  [⬅ Voltar]

  (clicou 📅 Ver por data)
  → Ano:
    [2026]  [2025]  (anos que existem no DB)
    [⬅ Voltar]

  (clicou 2026)
  → Mês:
    [04 Abril (8)]  [03 Março (3)]  (meses com contagem)
    [⬅ Voltar]

  (clicou 04 Abril)
  → Relatórios:
    [Steel Price Report — 15/04]
    [SBB Steel Markets Daily — 15/04]
    [Cement Weekly — 09/04]
    ...todos do mês
    [⬅ Voltar]

  (clicou qualquer relatório)
  → report_dl:<uuid> → sendDocument (handler já existe)
```

### Callback data format

| Nível | Callback data | Descrição |
|---|---|---|
| Menu tipos | `rpt_type:<reportType>` | Ex: `rpt_type:Market Reports` |
| Download | `rpt_dl:<uuid>` | Alias de `report_dl:<uuid>` (reusa handler existente) |
| Iniciar browse | `rpt_years:<reportType>` | Mostra anos disponíveis |
| Selecionar ano | `rpt_year:<reportType>:<year>` | Mostra meses desse ano |
| Selecionar mês | `rpt_month:<reportType>:<year>:<month>` | Lista relatórios do mês |
| Voltar | `rpt_back:types` | Volta pro menu de tipos |
| Voltar | `rpt_back:type:<reportType>` | Volta pros últimos 10 desse tipo |
| Voltar | `rpt_back:years:<reportType>` | Volta pros anos |
| Voltar | `rpt_back:year:<reportType>:<year>` | Volta pros meses desse ano |

### Message editing (not sending new)

Toda navegação usa `editMessageText` + `editMessageReplyMarkup` na mesma mensagem original. Evita flood de mensagens no chat. O `message_id` da resposta ao `/reports` é o que é editado em todos os callbacks subsequentes.

### Supabase queries

Todas contra tabela `platts_reports`:

**Últimos 10 de um tipo:**
```sql
SELECT id, report_name, date_key, frequency
FROM platts_reports
WHERE report_type = $1
ORDER BY date_key DESC
LIMIT 10
```

**Anos disponíveis de um tipo:**
```sql
SELECT DISTINCT EXTRACT(YEAR FROM date_key)::int AS year
FROM platts_reports
WHERE report_type = $1
ORDER BY year DESC
```

**Meses de um ano (com contagem):**
```sql
SELECT EXTRACT(MONTH FROM date_key)::int AS month, COUNT(*) AS cnt
FROM platts_reports
WHERE report_type = $1 AND EXTRACT(YEAR FROM date_key) = $2
GROUP BY month
ORDER BY month DESC
```

**Relatórios de um mês:**
```sql
SELECT id, report_name, date_key, frequency
FROM platts_reports
WHERE report_type = $1
  AND EXTRACT(YEAR FROM date_key) = $2
  AND EXTRACT(MONTH FROM date_key) = $3
ORDER BY date_key DESC, report_name
```

### Auth check

```python
if chat_id != int(os.environ.get("ADMIN_CHAT_ID", "0")):
    # silencioso — ignora comando de não-admin
    return jsonify({"ok": True})
```

### Registrar comandos via setMyCommands

Na inicialização do Flask app (ou via script one-time), registrar todos os comandos do bot:

```python
commands = [
    {"command": "reports", "description": "Consultar relatórios Platts (PDF)"},
    {"command": "help", "description": "Ajuda e comandos disponíveis"},
    {"command": "status", "description": "Status dos workflows"},
    {"command": "queue", "description": "Fila de itens pendentes"},
    {"command": "history", "description": "Histórico de entregas"},
    {"command": "rejections", "description": "Itens rejeitados"},
    {"command": "stats", "description": "Estatísticas gerais"},
    {"command": "reprocess", "description": "Re-processar item por ID"},
    {"command": "add", "description": "Adicionar item manual"},
    {"command": "list", "description": "Listar itens"},
]
telegram_api("setMyCommands", {"commands": commands})
```

Chamar uma vez na startup do Flask (ou no `/help` handler como side effect).

### Where changes happen

- **Apenas `webhook/app.py`** — ~120 linhas novas:
  - `/reports` command handler (~15 linhas)
  - 4 callback handlers: `rpt_type`, `rpt_years`/`rpt_year`/`rpt_month` (~80 linhas)
  - `rpt_back` handler (~15 linhas)
  - `setMyCommands` call na startup (~10 linhas)
- Zero mudanças no actor, Supabase schema, GitHub Actions

### Error handling

| Cenário | Comportamento |
|---|---|
| Supabase indisponível | Responde "Erro ao consultar relatórios" via `editMessageText` |
| Tipo sem relatórios | Mostra "Nenhum relatório encontrado" + botão Voltar |
| Ano/mês sem dados | Mostra "Nenhum relatório nesse período" + botão Voltar |
| `report_dl` falha | Handler existente já trata (answerCallbackQuery com erro) |
| Não-admin usa `/reports` | Ignora silenciosamente |

## Success criteria

1. `/reports` no chat → menu de tipos aparece com inline keyboard
2. Navegar até um relatório → `sendDocument` entrega o PDF
3. Toda navegação edita a mesma mensagem (sem flood)
4. "Ver por data" permite browse ano → mês → lista
5. Botão ⬅ Voltar funciona em todos os níveis
6. Menu `/` do Telegram lista todos os comandos com descrições
7. Não-admin é ignorado silenciosamente
