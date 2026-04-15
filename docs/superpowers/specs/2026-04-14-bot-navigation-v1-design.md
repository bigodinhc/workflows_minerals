# Bot Navigation v1 — Design

**Status:** Draft · 2026-04-14
**Parent project:** Antigravity WF (`webhook/app.py`)

## Goal

Adicionar descobribilidade e navegação ao bot do Telegram da Minerals Trading, com consulta direta aos dados no Redis e captura opcional de razão nas recusas — alimentando um loop de feedback pros prompts dos 3 agents ao longo do tempo.

## Motivação

Hoje o bot funciona (curadoria item-a-item + 3 agents + contatos + status), mas:

- **Sem descobribilidade.** Comandos (`/add`, `/list`, `/status`, `/reprocess`) ficam na cabeça do operador. Quem abre o bot pela primeira vez não sabe o que existe.
- **Sem navegação.** Quando items de staging rolam no scroll do Telegram, não tem como "listar o que ainda tá pendente". Idem pra arquivo.
- **Sem feedback loop.** Cliques em "Recusar" e "Ajustar" somem. Nenhuma métrica, nenhum contexto pra refinar prompts.
- **Sem visibilidade operacional.** Quantas notícias vieram hoje? Quantas o Curator processou? Essas perguntas só são respondíveis vasculhando logs do Railway.

## Escopo

### Dentro
- 5 comandos novos: `/help`, `/queue`, `/history`, `/stats`, `/rejections`
- Registro dos comandos via `setMyCommands` (autocomplete do Telegram)
- Captura opcional de razão nos dois fluxos de recusa (curadoria de item e recusa de draft)
- Módulos novos: `webhook/redis_queries.py`, `webhook/query_handlers.py`
- Novo keyspace Redis pro feedback + index em Sorted Set
- Testes unitários + integração leve

### Fora
- Splitar totalmente o `webhook/app.py` (fora de escopo — só criamos os 2 módulos novos; `app.py` continua grande)
- Busca full-text (`/find`) — descartada
- Histórico com filtro por data (`/history 13/04`) — apenas últimos 10 cross-date
- Analytics profundas (gráficos, trending topics) — fica pra v2
- Reprocessar feedback em loops de auto-melhoria de prompt — manual por ora
- Dashboard web com as queries — fora de escopo
- Alertas proativos (ex: "staging tem 50+ items acumulados") — fora de escopo

## Arquitetura

Seguindo o padrão já estabelecido por `webhook/contact_admin.py` (módulo separado pra fluxo de `/add`/`/list`):

```
webhook/
  app.py              # (existente) dispatch mínimo pros novos comandos + REJECT_REASON_STATE
  contact_admin.py    # (existente)
  query_handlers.py   # NOVO — formatação dos 5 comandos de consulta
  redis_queries.py    # NOVO — helpers puros de I/O no Redis
```

**Responsabilidades:**

- `redis_queries.py` — único lugar que sabe os keyspaces do Platts e os novos keys de feedback. Funções retornam dicts plain. Testável com `fakeredis`.
- `query_handlers.py` — formata strings (Markdown) pras respostas dos comandos, consome `redis_queries.py`. Também lida com a paginação do `/queue` e o callback `queue_open:<id>` que renderiza o card completo.
- `app.py` — só roteia: quando `/queue` chega, chama `query_handlers.handle_queue(chat_id, args)`. Mantém 1 state novo (`REJECT_REASON_STATE`) e 2 alterações nos handlers existentes de reject.

## Comandos

Todos exigem `contact_admin.is_authorized(chat_id)` (mesmo padrão de `/add`, `/list`, `/status`).

### `/help`

Mensagem única, texto plano agrupado:

```
*COMANDOS*

/queue — items aguardando
/history — arquivo (últimos 10)
/rejections — recusas (últimas 10)
/stats — contadores de hoje
/status — saúde do sistema
/reprocess <id> — re-dispara pipeline
/add, /list — contatos
/cancel — abortar fluxo
```

### `/queue`

Lista compacta paginada com 5 items por página (reusa padrão do `/list`):

```
*STAGING · 12 items*

1. CMRG Reabre Acesso BHP
2. Vale Reduz Guidance Q2
3. Port Hedland Pause
4. China Corta Produção
5. BDI Sobe 4,2%

[1/3]  [próximo ➡]
```

- Linhas numeradas com título truncado em 60 chars (`"…"` no final se precisar)
- Cada número tem um inline button invisível (callback `queue_open:<id>`) que abre o card completo com os 4 botões originais (Ler completo / Arquivar / Recusar / 3 Agents)
- Paginação `queue_page:<n>`
- Ordem: mais novo primeiro (por `created_at` do staging)

Se staging vazio:
```
*STAGING*

Nenhum item aguardando.
```

### `/history`

Últimos 10 arquivados, cross-date, ordem decrescente por `archived_at`:

```
*ARQUIVADOS · 10 mais recentes*

1. Bonds Municipais — 14/abr
2. IODEX US$ 106,05 — 14/abr
3. Greve Port Hedland — 13/abr
4. China Steel Bonds — 13/abr
...
```

- Sem botões (é histórico, não actionable)
- Título truncado em 60 chars
- Data no formato `DD/mmm`

### `/stats`

Snapshot do dia corrente:

```
*HOJE · 14/abr*

Scraped     43
Staging     12
Arquivados  18
Recusados    6
Pipeline     2
```

- `scraped` = `SCARD platts:seen:<today_iso>`
- `staging` = conta staging atual (não só de hoje — staging não tem dimensão de data)
- `arquivados` = conta archive `<today_iso>`
- `recusados` = feedback entries com `action in {"curate_reject", "draft_reject"}` dentro de `[today_00:00, today_23:59]`
- `pipeline` = `SCARD platts:pipeline:processed:<today_iso>`

### `/rejections`

Últimas 10 recusas com razão:

```
*RECUSAS · últimas 10*

1. 14h30 · "não é iron ore, é coking coal"
2. 13h12 · (sem razão)
3. 12h58 · "duplicata"
...
```

- Hora em formato `HH:mm` (UTC, consistente com resto do bot)
- Razão em itálico dentro de aspas; se vazia, mostra `_(sem razão)_`
- Truncar razão em 80 chars

### `setMyCommands`

Chamada única pra API do Telegram (`POST https://api.telegram.org/bot<token>/setMyCommands`) registrando:

```json
[
  {"command": "help", "description": "Lista todos os comandos"},
  {"command": "queue", "description": "Items aguardando curadoria"},
  {"command": "history", "description": "Últimos 10 arquivados"},
  {"command": "rejections", "description": "Últimas 10 recusas"},
  {"command": "stats", "description": "Contadores de hoje"},
  {"command": "status", "description": "Saúde dos workflows"},
  {"command": "reprocess", "description": "Re-dispara pipeline num item"},
  {"command": "add", "description": "Adicionar contato"},
  {"command": "list", "description": "Listar contatos"},
  {"command": "cancel", "description": "Abortar fluxo atual"}
]
```

Implementação: rota Flask `POST /admin/register-commands` protegida por `contact_admin.is_authorized` via header ou query param de chat_id. User dispara manualmente pela primeira vez; não roda a cada boot.

## Dados — Redis

### Keyspaces existentes (só leitura, sem mudança)

| Key | Tipo | Uso |
|---|---|---|
| `platts:staging:<id>` | String (JSON) | Items aguardando curadoria — alimenta `/queue` |
| `platts:archive:<date>:<id>` | String (JSON) | Arquivados — alimenta `/history` |
| `platts:seen:<date>` | Set | Dedup diário — alimenta contador `scraped` |
| `webhook:draft:<id>` | Hash | Drafts 3 agents (existente) |

### Keyspaces novos

```
webhook:feedback:<ts>-<id>     Hash   TTL 30d
  action     "curate_reject" | "draft_reject"
  item_id    <id do item ou draft>
  chat_id    <int>
  reason     <string>       # vazio = user pulou/timeout
  timestamp  <epoch seconds>
  title      <string>       # snapshot — preserva se item/draft expirar

webhook:feedback:index         Sorted Set   sem TTL
  members    "<ts>-<id>"
  scores     <ts como epoch>

platts:pipeline:processed:<date>   Set   TTL 2d
  members    <item_id>       # cada click em 3 Agents adiciona
```

**Por que Hash + Sorted Set:**
- Hash: campos atômicos, HGETALL é O(N) mas N é pequeno (~6 campos)
- Sorted Set: `ZRANGE feedback:index -10 -1 REV` busca últimos 10 em O(log N) sem varredura
- Index não tem TTL pq os members expiram com os hashes (após 30d ficam apontando pra nada — cleanup opcional por cron, low priority)

### Funções em `redis_queries.py`

```python
list_staging(limit: int = 50) -> list[dict]
    # Retorna items de staging ordenados por created_at DESC
    # Cada dict contém: id, title, source, tabName, created_at, raw_json

list_archive_recent(limit: int = 10) -> list[dict]
    # Cross-date, ordenados por archived_at DESC
    # Cada dict: id, title, archived_at (ISO), archived_date

stats_for_date(date_iso: str) -> dict
    # {scraped, staging, archived, rejected, pipeline}

save_feedback(action: str, item_id: str, chat_id: int,
              reason: str, title: str) -> str
    # Cria Hash + member no index
    # Retorna a feedback_key "<ts>-<id>"

update_feedback_reason(feedback_key: str, reason: str) -> bool
    # HSET no campo reason
    # Retorna True se updated, False se key não existe

list_feedback(limit: int = 10,
              action: str | None = None,
              since_ts: int | None = None) -> list[dict]
    # Pega últimos N do index, HGETALL em cada
    # Filtros: action (equals), since_ts (>= epoch)

mark_pipeline_processed(item_id: str, date_iso: str) -> None
    # SADD no platts:pipeline:processed:<date_iso>
    # Chamado pelo handler curate_pipeline no webhook/app.py
```

Contadores com `KEYS platts:staging:*`: aceitável em produção porque o staging típico tem <100 items (TTL 48h + fluxo ativo). Se crescer, trocar por `SCAN` ou manter um index Set auxiliar — fora de escopo.

## UX — Captura de razão no "Recusar"

Aplica em:
1. `curate_reject` — callback dos cards de curadoria (item recém-scraped)
2. `reject` — callback dos drafts dos 3 agents (mensagem AI pronta)

### Fluxo

```
User clica "❌ Recusar"
  │
  ├─ answer_callback_query("❌ Recusado")
  ├─ redis_queries.save_feedback(action, item_id, chat_id, reason="", title=<snapshot>)
  │    → retorna feedback_key
  ├─ [ação original do handler]
  │    curate_reject → redis_client.discard(item_id)
  │    reject → drafts_update(draft_id, status="rejected")
  ├─ finalize_card(chat_id, callback_query,
  │     "❌ *Recusado* em HH:MM UTC\nPor quê? (opcional, responda ou `pular`)")
  └─ REJECT_REASON_STATE[chat_id] = {
         "feedback_key": <key>,
         "expires_at": now + 120 seconds
     }
```

### Próxima mensagem do user

Em `handle_message`, checar em cascata:
1. `ADJUST_STATE[chat_id]` (existente — precedência sobre reject)
2. `REJECT_REASON_STATE[chat_id]` (novo)

Lógica do state:
```
se state existe E now < state.expires_at:
    texto = mensagem.strip().lower()
    se texto in {"pular", "skip"}:
        limpa state
        send_telegram_message(chat_id, "✅ Ok, sem razão registrada.")
    senão:
        redis_queries.update_feedback_reason(state.feedback_key, mensagem.strip())
        limpa state
        send_telegram_message(chat_id, "✅ Razão registrada.")
    return  # não cai no fluxo normal

se state existe E now >= state.expires_at:
    limpa state silenciosamente
    # (segue pro fluxo normal)
```

### Timeout

2 minutos (120s). Justificativa: se user quisesse registrar razão, faria em ~30s. Após 2 min provavelmente o foco mudou (colou nova notícia pra processar, etc.). Timeout evita "grudar" no state.

### Concorrência

`REJECT_REASON_STATE` e `ADJUST_STATE` são mutuamente exclusivos na prática (não dá pra estar ajustando draft E recusando item simultaneamente no mesmo chat). Mas defensivo: adjust vence (checagem primeiro no cascade).

State é in-memory (dict Python no processo Flask). Se Railway redeployar mid-fluxo, state perde — user manda razão, bot não reconhece, vira mensagem normal. Aceitável porque já salvamos o feedback `reason=""` no click — pior caso é razão perdida, não dado corrompido.

## Testes

Seguindo TDD (padrão do projeto).

**`tests/test_redis_queries.py`** — com `fakeredis`:
- `test_list_staging_sorted_by_created_at`
- `test_list_staging_empty`
- `test_list_staging_respects_limit`
- `test_list_archive_recent_crossdate`
- `test_list_archive_recent_empty`
- `test_stats_for_date_all_zero`
- `test_stats_for_date_populated`
- `test_save_feedback_creates_hash_and_index`
- `test_save_feedback_empty_reason_allowed`
- `test_save_feedback_preserves_title_snapshot`
- `test_update_feedback_reason_updates_hash`
- `test_update_feedback_reason_nonexistent_returns_false`
- `test_list_feedback_most_recent_first`
- `test_list_feedback_filter_by_action`
- `test_list_feedback_filter_since_ts`
- `test_mark_pipeline_processed_idempotent`

**`tests/test_query_handlers.py`** — formatação:
- `test_help_text_format`
- `test_queue_empty_message`
- `test_queue_single_page`
- `test_queue_paginated_10_items`
- `test_queue_truncates_long_title`
- `test_history_empty`
- `test_history_format_matches_spec`
- `test_stats_format_aligned_columns`
- `test_rejections_empty`
- `test_rejections_with_and_without_reason`
- `test_rejections_truncates_long_reason`

**`tests/test_reject_reason_flow.py`**:
- `test_reject_state_set_on_click`
- `test_reject_feedback_saved_immediately_with_empty_reason`
- `test_reason_text_updates_feedback`
- `test_pular_clears_state_no_update`
- `test_skip_english_also_works`
- `test_state_expires_after_120_seconds`
- `test_adjust_state_takes_precedence`

**`tests/test_query_commands_integration.py`** — via Flask test client:
- `test_queue_unauthorized_returns_silently`
- `test_help_authorized_returns_command_list`
- `test_stats_returns_today_counts`
- `test_history_with_empty_archive`

**Total estimado:** ~28 testes novos.

## Riscos e mitigações

| Risco | Mitigação |
|---|---|
| `KEYS` em Redis bloqueia prod | Staging <100 items por TTL+fluxo; se crescer, trocar por SCAN |
| State in-memory perde em redeploy | Razão já salva no click (vazia); pior caso = razão não registrada |
| Timeout muito curto (2 min) frustra user | Ajustável via constante `REJECT_REASON_TIMEOUT_SECONDS` |
| `setMyCommands` chamada errada sobrescreve | Rota protegida por autorização; bot continua funcional mesmo se chamada falhar |
| Hash+Sorted Set orphan members após TTL | Cleanup manual opcional via cron; não bloqueia leitura |

## Gotchas do ambiente

- **Path do projeto tem trailing space:** `/Users/bigode/Dev/Antigravity WF ` (espaço final). Bash sempre `cd /Users/bigode/Dev/Antigravity\ WF\ `.
- **No `webhook/app.py` evitar imports inline** dentro de funções — Python marca variável como local pro escopo inteiro e quebra outros branches com `UnboundLocalError`. Manter imports no topo do módulo.
- **Telegram Markdown:** escapar `_ * ` [` em campos dinâmicos (títulos de notícia, razões de recusa) antes de interpolar com `parse_mode="Markdown"`, senão API retorna 400.

## Dependências

Nenhuma nova lib Python. Usa:
- `redis` (já em `requirements.txt`)
- `flask` (já)
- `requests` (já, pra chamar Telegram API)
- `fakeredis` em dev (já em testes existentes)

## Entregáveis

1. `webhook/redis_queries.py` — ~200 linhas
2. `webhook/query_handlers.py` — ~250 linhas
3. Modificações em `webhook/app.py`:
   - 5 dispatches novos em `handle_message`
   - `REJECT_REASON_STATE` dict
   - Cascade check no `handle_message` pro novo state
   - Alteração em 2 handlers de reject (salvar feedback + set state)
   - Rota `POST /admin/register-commands`
4. ~28 testes novos em `tests/`
5. Commit no main (seguindo padrão: commits pequenos por task)
