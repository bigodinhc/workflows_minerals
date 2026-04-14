# Contact Management via Telegram Bot — Design Doc

**Data:** 2026-04-14
**Autor:** Colaboracao Claude + bigodinhc
**Status:** Draft — aguardando review

---

## 1. Problema

Hoje a lista de contatos de WhatsApp (~100 pessoas, Google Sheets "Página1") so pode ser gerenciada editando a planilha manualmente:
- Adicionar contato = editar planilha na web
- Desativar temporariamente = editar celula `ButtonPayload` de "Big" para outro valor

Isso e inconveniente quando o usuario esta no celular ou so quer fazer uma mudanca rapida. Tambem nao permite busca rapida pela lista.

Alem disso, o `DeliveryReporter` exibe `"—"` como nome de todos os contatos nos relatorios porque procura colunas `Nome`/`Name` que nao existem — a coluna real e `ProfileName`.

## 2. Objetivos

- Adicionar/desativar/reativar contatos direto pelo bot do Telegram (so admin autorizado)
- Listar contatos com paginacao e busca, com botoes inline pra toggle visual
- Preservar historico (desativar nao apaga, so muda `ButtonPayload` de "Big" pra "Inactive")
- Corrigir nome em relatorios de delivery (`ProfileName` → usado como fonte do nome)
- Extrair helper `build_contact_from_row` pra eliminar duplicacao (5 callers hoje)

## 3. Nao-Objetivos

- Multi-admin (varios `chat_id` autorizados) — futuro
- Migracao pra Supabase — futuro
- Editar nome de contato existente (so add/toggle) — futuro; editar direto na planilha se precisar
- Import batch via CSV — futuro
- Confirmacao com botao antes de add — nao, executa direto (reverte via toggle)

## 4. Escopo e Decisoes Acordadas

### 4.1 — Bug fix (junto neste spec)
Relatorios de delivery vao exibir nome real do contato (coluna `ProfileName` da planilha).

### 4.2 — Feature A: `/add` em 2 passos
1. Usuario envia `/add`
2. Bot responde com formato esperado (`Nome Telefone` + exemplo)
3. Usuario envia dados na mensagem seguinte
4. Bot valida, grava, confirma

### 4.3 — Feature B: `/list [busca]` com toggle + paginacao
- 10 contatos por pagina
- Cada contato e um botao wide com emoji de status (`✅` ativo / `❌` inativo)
- Click no contato alterna status diretamente
- Navegacao com botoes `◀` `▶`
- Busca parcial case-insensitive por `ProfileName`

### 4.4 — Desativacao
Muda `ButtonPayload` de "Big" para "Inactive". Reativacao inverte. Preserva dados originais.

### 4.5 — Autorizacao
Whitelist por `TELEGRAM_CHAT_ID` (ja existente em env). Requests de outro `chat_id` sao ignorados silenciosamente. Callbacks retornam "Nao autorizado".

### 4.6 — Duplicata em `/add`
Rejeita com info do contato existente. Nao sobrescreve.

## 5. Arquitetura

```
Telegram (voce)
     │  /add, /list, /cancel, "Joao 5511..."
     ▼
Webhook /webhook (Flask, Railway)
     │
     ├─► Autorizacao: chat_id == TELEGRAM_CHAT_ID
     │
     ├─► Roteamento:
     │    ├── "/add"              → contact_admin.handle_add_start()
     │    ├── "/cancel"           → contact_admin.handle_cancel()
     │    ├── "/list [busca]"     → contact_admin.handle_list()
     │    ├── texto livre + ADMIN_STATE==awaiting_add → contact_admin.handle_add_data()
     │    ├── callback "pg:N[:busca]"    → contact_admin.handle_pagination()
     │    └── callback "tgl:<phone>"     → contact_admin.handle_toggle()
     │
     ▼
SheetsClient (gspread)
     │
     └─► Google Sheets "Página1"
```

### 5.1 — Novo modulo

`webhook/contact_admin.py` — logica pura de comandos e callbacks. Mantem `app.py` enxuto.

### 5.2 — Estado em memoria

```python
ADMIN_STATE = {}  # chat_id → {"awaiting": "add_data", "expires_at": datetime}
```

Padrao identico ao `ADJUST_STATE` existente.

### 5.3 — SheetsClient: 3 metodos novos

```python
def add_contact(self, sheet_id: str, profile_name: str, phone: str) -> None:
    """Append row com valores padrao. Copia 'To' da ultima linha."""

def toggle_contact(self, sheet_id: str, phone: str) -> tuple[str, str]:
    """Flip Big↔Inactive no ButtonPayload. Retorna (profile_name, novo_status)."""

def list_contacts(
    self, sheet_id: str, search: str | None = None,
    page: int = 1, per_page: int = 10,
) -> tuple[list[dict], int]:
    """Retorna (contatos_da_pagina, total_paginas). Filtra por ProfileName se search."""
```

### 5.4 — Helper compartilhado

Novo em `execution/core/delivery_reporter.py`:

```python
def build_contact_from_row(row: dict) -> Optional[Contact]:
    """Converte linha do sheet em Contact. Usado por scripts e webhook."""
    name = (
        row.get("ProfileName")  # nova primeira prioridade
        or row.get("Nome")
        or row.get("Name")
        or "—"
    )
    raw_phone = (
        row.get("Evolution-api")
        or row.get("n8n-evo")
        or row.get("Telefone")
        or row.get("Phone")
        or row.get("From")
    )
    if not raw_phone:
        return None
    phone = (
        str(raw_phone)
        .replace("whatsapp:", "")
        .replace("@s.whatsapp.net", "")
        .replace("+", "")
        .strip()
    )
    return Contact(name=name, phone=phone)
```

Substitui `build_contact()` duplicado em 5 callers.

## 6. Formato das Mensagens

### 6.1 — `/add` passo 1

```
📝 ADICIONAR CONTATO

Envie no formato:
`Nome Telefone`

Exemplo: `Joao Silva 5511999999999`

Use /cancel pra desistir.
```

Define `ADMIN_STATE[chat_id] = {"awaiting": "add_data", "expires_at": now + 5min}`.

### 6.2 — `/add` passo 2 — sucesso

```
✅ Joao Silva adicionado
Total ativos: 101
```

Limpa `ADMIN_STATE[chat_id]`.

### 6.3 — `/add` passo 2 — erros

| Input invalido | Resposta |
|----------------|----------|
| `"Joao"` (falta telefone) | `❌ Formato invalido. Envie: Nome Telefone` |
| `"Joao abc"` | `❌ Telefone invalido. So digitos. Ex: 5511999999999` |
| `"Joao 12345"` | `❌ Telefone muito curto (minimo 10 digitos)` |
| Duplicado ativo | `❌ Ja existe: "Joao Silva" (ativo)` |
| Duplicado inativo | `❌ Ja existe: "Joao Silva" (desativado). Reative via /list` |

ADMIN_STATE permanece em `awaiting_add` ate sucesso ou `/cancel`.

### 6.4 — `/list` renderizado

```
📋 CONTATOS (103) — Pagina 1/11

Toque pra ativar/desativar.

[✅ Adriano Francino — 553791000123]
[✅ Alesson — 553798721100]
[✅ ALEXANDRE JAFET — 553188991076]
[❌ Alexandre Farah — 5531991981338]
[✅ Alexandre Leme — 554191017174]
[✅ Alexandre Lima-Pinarello — 553188071449]
[✅ Alisson Bombas Diesel — 553788270656]
[✅ Aloysio - Pessoal — 553191369135]
[✅ Ana Beatriz Arcanjo — 553192176496]
[✅ Ana Lobato — 553198023713]

[◀]  [Pagina 1/11]  [▶]
```

### 6.5 — `/list joao` (com busca)

```
📋 RESULTADO BUSCA "joao" (3)

[✅ Joao Silva — 5511999999999]
[❌ Joao Pedro — 5521888888888]
[✅ Joao Alberto — 5531777777777]
```

Sem navegacao se ≤ per_page. Com navegacao se > per_page, mantendo o parametro de busca no `callback_data`.

### 6.6 — `/list` sem resultados

```
📋 Nenhum contato encontrado pra "xyz"
```

ou

```
📋 Nenhum contato cadastrado. Use /add
```

### 6.7 — `/cancel`

```
Cancelado.
```

Limpa `ADMIN_STATE[chat_id]`. No-op se nao havia estado.

## 7. Callbacks

### 7.1 — Formato `callback_data` (limite Telegram: 64 bytes)

| Acao | Formato | Exemplo |
|------|---------|---------|
| Toggle contato | `tgl:<phone>` | `tgl:553791000123` |
| Navegar | `pg:<N>[:busca]` | `pg:3` ou `pg:2:joao` |
| No-op (botao central de pagina) | `nop` | `nop` |

### 7.2 — Handler `tgl:<phone>`

1. Verifica autorizacao. Se nao autorizado → `answerCallbackQuery("Nao autorizado")`.
2. `toggle_contact(sheet_id, phone)` → retorna `(name, new_status)`.
3. Re-renderiza a mensagem via `editMessageText` (mesma pagina, mesma busca — extrai do contexto da mensagem ou guarda em estado).
4. `answerCallbackQuery` com toast:
   - `"✅ Joao Silva ativado"` se new_status == "Big"
   - `"❌ Joao Silva desativado"` se new_status == "Inactive"

### 7.3 — Handler `pg:<N>[:busca]`

1. Verifica autorizacao.
2. Re-renderiza a mensagem com pagina N (mesma busca se vier).
3. `answerCallbackQuery` silencioso (sem toast).

### 7.4 — Handler `nop`

`answerCallbackQuery` silencioso. Nada mais.

## 8. Gravacao na Planilha (Feature A — `/add`)

Colunas e valores na nova linha:

| Coluna | Valor |
|--------|-------|
| `ProfileName` | nome recebido (ex: "Joao Silva") |
| `MessageType` | `button` |
| `SmsStatus` | `received` |
| `Body` | `Sim, quero receber (via bot)` |
| `From` | `whatsapp:+<digits>` (ex: `whatsapp:+5511999999999`) |
| `ButtonPayload` | `Big` |
| `To` | copiado da ultima linha existente da planilha |
| `n8n-evo` | `<digits>@s.whatsapp.net` (ex: `5511999999999@s.whatsapp.net`) |

**Por que duas colunas de telefone (`From` e `n8n-evo`)?** Contatos existentes tem ambas preenchidas. O pipeline de envio pode ler qualquer uma — mantemos consistencia.

**Por que copiar `To` da ultima linha?** Contatos ja existentes tem o mesmo valor (numero oficial do WhatsApp da empresa). Copiar da ultima linha preserva o padrao sem exigir config nova.

## 9. Validacao de Telefone

Input bruto do usuario pode vir como:
- `5511999999999`
- `+5511999999999`
- `5511999999999@s.whatsapp.net`
- `+55 (11) 99999-9999`

Normalizacao:
1. Remove `+`, `whatsapp:`, `@s.whatsapp.net`, espacos, parenteses, hifens
2. Valida: so digitos, 10-15 chars (DDI + DDD + numero)

Se passar → armazena apenas os digitos e reconstroi formato nas colunas.

## 10. Autorizacao e Erros

### 10.1 — Regra
Comparacao `str(chat_id) == TELEGRAM_CHAT_ID`. Se falso:
- Mensagem de texto → bot nao responde nada (ignorar silenciosamente)
- Callback → `answerCallbackQuery("Nao autorizado")` e aborta

### 10.2 — Erros do Sheets
Qualquer excecao do gspread em `add_contact`/`toggle_contact`/`list_contacts`:
- Loga com stack trace
- Responde ao usuario: `❌ Erro ao acessar planilha. Tente novamente.`
- Nao expoe mensagem de erro bruta (evita vazar detalhes internos)

### 10.3 — Expiracao do ADMIN_STATE
`expires_at` = now + 5 min. Checado antes de cada uso. Se expirado, trata como estado inexistente (bot interpreta como texto livre ou novo comando).

## 11. Testes

Arquivo novo: `tests/test_contact_admin.py`

| Teste | Verifica |
|-------|----------|
| `test_parse_add_input_valid` | `"Joao Silva 5511999999999"` → `("Joao Silva", "5511999999999")` |
| `test_parse_add_input_missing_phone` | `"Joao Silva"` → raises `ValueError` |
| `test_parse_add_input_strips_prefixes` | `"Joao +5511999@s.whatsapp.net"` → phone normalizado |
| `test_parse_add_input_rejects_short` | `"Joao 12345"` → raises |
| `test_parse_add_input_rejects_non_digits` | `"Joao 5511abc"` → raises |
| `test_parse_add_input_multiword_name` | `"Ana Maria Santos 5511..."` → name="Ana Maria Santos" |
| `test_authorization_rejects_wrong_chat` | chat_id errado → retorna None / sem efeito |
| `test_authorization_accepts_admin` | chat_id correto → fluxo normal |
| `test_toggle_big_to_inactive` | Mock row com ButtonPayload="Big" → "Inactive" |
| `test_toggle_inactive_to_big` | Reverso |
| `test_list_pagination` | 25 contatos, per_page=10, page=2 → items [10:20], total_pages=3 |
| `test_list_search_case_insensitive` | busca "JOAO" e "joao" batem com "Joao Silva" |
| `test_list_search_no_matches` | busca sem match → (list vazia, 0 paginas) |
| `test_duplicate_add_rejected_active` | phone existe como ativo → raises com info |
| `test_duplicate_add_rejected_inactive` | phone existe como inativo → raises com info |
| `test_admin_state_cleared_on_cancel` | `/cancel` → ADMIN_STATE limpo |
| `test_admin_state_cleared_on_new_command` | `/list` apos `/add` sem dados → limpa estado |
| `test_admin_state_expired_ignored` | expires_at no passado → tratado como nao-existente |
| `test_build_contact_profile_name_priority` | row com ProfileName + Nome → usa ProfileName |
| `test_build_contact_falls_back_to_nome` | row so com Nome → usa Nome |
| `test_build_contact_phone_normalization` | remove `+`, `whatsapp:`, `@s.whatsapp.net` |

Mocks pra gspread — nenhum teste chama API real.

## 12. Plano de Rollout

1. **Fix ProfileName (commit atomico independente):**
   - Extrai `build_contact_from_row()` em `execution/core/delivery_reporter.py`
   - Atualiza os 5 callers (morning_check, daily_report, baltic, send_news, webhook.process_approval_async)
   - Testes unitarios do helper
   - Deploy imediato: proximo cron mostra nomes reais nos relatorios do Telegram

2. **SheetsClient helpers + testes:**
   - `add_contact`, `toggle_contact`, `list_contacts`
   - Testes com mocks do gspread

3. **contact_admin module + testes:**
   - Parser, autorizacao, formatacao de mensagens
   - ADMIN_STATE management com expiracao
   - 100% offline, testavel sem Telegram real

4. **Integracao no webhook/app.py:**
   - Roteamento de comandos/callbacks novos
   - Update do `/start` mencionando `/add` e `/list`
   - Deploy no Railway (auto via push)

5. **Teste manual pelo usuario:**
   - `/add` com nome/telefone reais
   - `/list` com e sem busca, toggle ida e volta, navegacao
   - Valida que proximo cron pega o novo contato (ou exclui o desativado)

Cada etapa e revertivel isoladamente.

## 13. Riscos e Mitigacoes

| Risco | Probabilidade | Mitigacao |
|-------|---------------|-----------|
| Planilha muda de schema (coluna renomeada) | Baixa | Helper `build_contact_from_row` com multiplos fallbacks |
| gspread quota exceeded com listagem frequente | Baixa | Cache leve de 30s na `list_contacts` se virar problema (fora de escopo agora) |
| Callback_data com phone > 64 bytes | Muito baixa | Phones tem 10-15 digitos, `tgl:` prefix = 4 bytes. Sempre < 20 bytes. |
| 2 admins futuros clicando toggle simultaneamente | Futuro | Lock distribuido se adicionar multi-admin |
| Admin envia `/add` e esquece de completar | Media | Expiracao em 5 min + auto-cancel ao receber novo comando |

## 14. Questoes em Aberto

Nenhuma. Todas as decisoes foram acordadas:
- [x] Desativacao via ButtonPayload: "Big" ↔ "Inactive"
- [x] Autorizacao por `TELEGRAM_CHAT_ID` (1 admin)
- [x] `/add` em 2 passos
- [x] `/list` paginado com toggle
- [x] Fix do ProfileName junto
- [x] Sem confirmacao (reverte via toggle)
- [x] Formato callback: `tgl:<phone>`, `pg:<N>[:busca]`
- [x] Copia `To` da ultima linha ao adicionar
