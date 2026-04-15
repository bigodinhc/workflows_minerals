# Bot Navigation v1.1 — Digest Ingestion & Message Polish

**Status:** Draft · 2026-04-15
**Parent project:** Antigravity WF (`webhook/app.py`)
**Builds on:** `2026-04-14-bot-navigation-v1-design.md`

## Goal

Reduzir ruído no chat do operador e dar polish visual nas mensagens do bot, mantendo o modelo de curadoria manual mas **unificando os dois fluxos** (notícia comum + rationale) numa única caixa de entrada.

## Motivação

Depois da v1 (5 comandos navegacionais + captura de razão), três dores permanecem:

1. **Scrap polui o chat.** Cada notícia scrappada vira UM card no Telegram. Um batch de 10-20 items enche a tela inteira e o operador perde o que já decidiu.
2. **Rationale é invisível.** Items classificados como "rationale" (RMW/Lump) hoje são auto-processados pelo `rationale_dispatcher` sem passar pelo operador. Ele não sabe quando roda nem o que saiu do outro lado.
3. **Mensagens de feedback são feias.** Confirmações ("Arquivado em HH:MM / ID"), progresso ("Processando com IA 1/3"), e outputs dos novos comandos (`/stats`, `/queue`) são funcionais mas visualmente rasos — pouca estrutura, poucos emojis, labels confusos ("Pipeline" não diz nada pro operador, "3 Agents" esconde o que o sistema faz).

## Escopo

### Dentro
- **Modelo de digest**: scrap deixa de postar card por item. No final do batch, manda UMA mensagem com contagem + preview de 3 títulos + botão "Abrir fila".
- **Digest só quando há novos**: batch com 0 novos = silêncio. Heartbeat é responsabilidade do `/status`, não do chat.
- **Unificação curação + rationale**: `router.py` deixa de rotear rationale pro dispatcher automático. Todos os items vão pra staging com um campo `type` (`"news"` ou `"rationale"`).
- **Ícone por tipo**: 🗞️ notícia · 📊 rationale. Aparece no digest, no `/queue`, no `/history`, e no título do card.
- **Botão unificado**: card de curadoria tem os mesmos 4 botões pra ambos os tipos (Ler completo / Arquivar / Recusar / Writer). Rationale também entra no pipeline dos 3 agents via "Writer".
- **Rename "3 Agents" → "Writer"** em TODAS as superfícies (botão do card, confirmação "Enviado para o Writer", label em `/stats`).
- **`/queue` com títulos como botões**: cada item vira 1 botão linha-cheia `🗞️ Título reduzido` ao invés de "1. Abrir". Callback continua `queue_open:<id>`.
- **Progress play-by-play**: a mensagem "⏳ Processando..." editada in-place vira um display de 3 fases com ⏳/✅ por agente (Writer → Reviewer → Finalizer).
- **Polish visual**: `/stats`, `/history`, `/rejections`, e confirmações (arquivado/recusado/enviado-writer) ganham emojis por linha, separador visual (`────`), e formato consistente (🕒 HH:MM · 🆔 id curto).

### Fora
- **Bug de duplicatas no scrap** — `platts:seen:<date>` às vezes deixa passar items repetidos. Não é polish. Fica pra um fix separado depois desse.
- **Deletar `rationale_dispatcher.py`** — depois que rationale passa pela curadoria, o auto-dispatcher fica órfão. Decisão: **mantém no codebase com um comentário TODO no topo** marcando "órfão após v1.1, revisitar em fase futura" (pode ser útil chamado manualmente via script).
- **Rebatizar outras mensagens que não estão na lista acima** (ex: `/start`, `/help`, erros de autenticação) — mantém como está; se incomodar depois, vira v1.2.
- **Ajustes nos prompts dos 3 agents** (Writer/Reviewer/Finalizer) — **fica pra próxima fase dedicada de prompts**. Rationale items vão passar pelos prompts atuais como se fossem notícia comum; se o output ficar ruim, é sinal pra priorizar o spec de ajuste de prompts.
- **Mudanças no WhatsApp formatter** — fora de escopo. Só reorganiza o feedback na tela do operador.

## Arquitetura

```
execution/
  curation/
    router.py              # (MODIFICADO) classify vira annotate
                           #              _stage_and_post → só stage (sem post_for_curation)
                           #              route_items retorna counters + items list
    rationale_dispatcher.py  # (SEM MUDANÇA imediata, fica dead após esta fase)
    telegram_poster.py     # (MODIFICADO) aceita type no item para ícone

execution/scripts/
  platts_ingestion.py      # (MODIFICADO) depois de route_items, se counters > 0
                           #              envia digest ao chat

webhook/
  app.py                   # (MODIFICADO)
                           # - curate_pipeline usa "Writer" em mensagens
                           # - progress_msg_id atualizado em 3 fases (writer/reviewer/finalizer)
                           # - renames cosméticos ("Enviado aos 3 agents" → "Enviado para o Writer")
  query_handlers.py        # (MODIFICADO)
                           # - format_stats: emoji por linha, label "No Writer"
                           # - format_queue_page: botões com título, não "N. Abrir"
                           # - format_history/format_rejections: separador + ícones
  digest.py                # (NOVO) formatter pro digest do scrap
                           #        função única: format_ingestion_digest(counters, preview)

execution/core/
  agents_progress.py       # (NOVO) helper pra gerar o texto de progresso 3-fases
                           #        format_pipeline_progress(current_phase, phases_done)
```

**Responsabilidades por módulo:**

- **`router.py`**: classifica e stageia. Não posta nada no Telegram. Item dict ganha campo `type: "news" | "rationale"`.
- **`platts_ingestion.py`** (ou whichever is the scrap entry point): orquestra scrap → route → se houver novos, envia digest único via `send_telegram_message`. É o único lugar que conhece o chat_id.
- **`digest.py`**: formatter puro. Input: counters + lista curta de items (top 3 por `stagedAt` desc). Output: (text, markup) com botão "🔍 Abrir fila" que abre `/queue` internamente (callback `queue_page:1`).
- **`agents_progress.py`**: formatter puro do play-by-play. Chamado em cada edit_message durante o pipeline.
- **`query_handlers.py`** e **`app.py`**: mudanças cosméticas que refinam o output dos comandos e as confirmações.

## Digest — formato final

Quando o scrap termina com N > 0 items novos:

```
📥 Ingestão · 7 novas
├ 🗞️ 5 notícias
└ 📊 2 rationale

• 🗞️ Iron Ore Inventory Declines
• 📊 IODEX Daily Rationale
• 🗞️ China Exports Up Q2
+4 mais

[🔍 Abrir fila]
```

Regras:
- Contagem por tipo é **omitida** quando um tipo é 0 (ex: só notícias → esconde a linha "rationale 0").
- Preview mostra até 3 títulos (mais novos por `stagedAt`), truncados em 60 chars (reusa `_truncate`).
- "+N mais" só aparece quando `total > 3`.
- Botão: `🔍 Abrir fila` com callback `queue_page:1` (mesmo que o `/queue` de hoje renderiza).
- Títulos com markdown especial (`*`, `_`, `[`, `]`, `` ` ``) escapados com `_escape_md`.

Edge cases:
- 0 novos → não envia nada. Log INFO pro observability.
- Todos rationale (0 notícias) → "├ 🗞️ 0 notícias" sumiu, mostra só a linha rationale. Na prática é mostrar só a árvore não-zero; se resultar em 1 linha só, ainda vale a estrutura de árvore pra consistência visual.
- `send_telegram_message` falha → log ERROR, mas items continuam em staging. Operador pode descobrir via `/queue`.

## `/queue` — botões com títulos

Hoje:
```
*STAGING · 3 items*

1. Iron Ore Inventory...
2. China Exports Q2
3. IODEX US$ 106,05

[1. Abrir]
[2. Abrir]
[3. Abrir]
[1/1]
```

Proposta:
```
*🗂️ STAGING · 3 items*

[🗞️ Iron Ore Inventory Declines]
[📊 IODEX Daily Rationale]
[🗞️ China Exports Up Q2]
[1/1]
```

- Texto "linhas numeradas acima dos botões" some — redundante.
- Botão único por item, linha cheia, com ícone por tipo + título truncado em ~40 chars (título + ícone + espaço + truncamento = cabe em 64-char limit do Telegram inline_keyboard text).
- Paginação idêntica (mesma lógica, só ajusta renderização).
- Callback de cada botão continua `queue_open:<id>`.

## `/stats` — polimento

Hoje:
```
*HOJE · 15/abr*

Scraped     43
Staging     12
Arquivados  18
Recusados    6
Pipeline     2
```

Proposta:
```
*📊 HOJE · 15/abr*
────────────────────
🔎 Scraped        43
🗂️ Staging        12
📦 Arquivados     18
❌ Recusados       6
🖋️ No Writer       2
```

- Título ganha emoji 📊.
- Separador visual em `────`.
- Emoji por linha (um visual verbo por contador).
- "Pipeline" vira "No Writer" — deixa claro que são items que foram pro agente Writer hoje.
- `platts:pipeline:processed:<date>` (keyspace) **não muda** — é só label. Backward compat preservado.
- Padding/alinhamento: emojis + espaços + número; números alinhados à direita do label visualmente (aproximado — Telegram não tem monospace em texto normal, só em `code` blocks; aceitar drift se emojis quebrarem alinhamento perfeito).

## `/history` e `/rejections` — polimento

`/history`:
```
*📚 ARQUIVADOS · 10 mais recentes*
────────────────────
1. 🗞️ Bonds Municipais — 14/abr
2. 📊 IODEX US$ 106,05 — 14/abr
3. 🗞️ Greve Port Hedland — 13/abr
```

`/rejections`:
```
*💭 RECUSAS · últimas 3*
────────────────────
1. 🕒 14:30 · "não é iron ore, é coking coal"
2. 🕒 13:12 · _(sem razão)_
3. 🕒 12:58 · "duplicata"
```

- Títulos ganham emoji temático.
- Separador `────`.
- `/history` ganha ícone por tipo (🗞️/📊) — requer gravar `type` no item arquivado (já será feito na Task 0 desta fase: quando router stageia com type, archive preserva).

## Card de curadoria — ajustes mínimos

Formato hoje (via `telegram_poster.post_for_curation`):
```
[preview text]

📰 Platts
🔖 Iron Ore News
📅 14/04/2026 13:46 UTC
🆔 `17c1e97db96c`

[📖 Ler completo] [✅ Arquivar] [❌ Recusar] [🤖 3 Agents]
```

Proposta:
```
*🗞️ Iron Ore Inventory Declines*

[preview text]

📅 `14/04 13:46 UTC` · 📰 Platts · 🔖 Iron Ore News
🆔 `17c1e97db96c`

[📖 Ler completo] [✅ Arquivar]
[❌ Recusar]       [🖋️ Writer]
```

- **Título ganha ícone de tipo** (🗞️/📊) + bold no topo — facilita escaneamento rápido.
- Meta-linhas compactadas em 1 linha com separador `·` (economiza 2 linhas verticais).
- Data abreviada (`14/04 13:46 UTC` ao invés de `14/04/2026 13:46 UTC`) — ano raramente é interessante pro operador que revisa hoje.
- Botões em 2 linhas de 2 (ao invés de 4 em linha) — melhor pra mobile.
- `🤖 3 Agents` → `🖋️ Writer`.

## Progress do pipeline — 3 fases in-place

Hoje o código (`webhook/app.py:852`) faz:
```python
edit_message(chat_id, progress_msg_id, "⏳ Processando com IA (1/3 Writer)...")
# ... mais edits genéricos
edit_message(chat_id, progress_msg_id, "✅ Processamento concluído!")
```

Proposta: substituir por 4 estados que refletem explicitamente Writer → Reviewer → Finalizer:

**Estado 1 (imediatamente após o click em "🖋️ Writer"):**
```
🖋️ *Writer* escrevendo... (1/3)
────────────────────
⏳ Writer
⏳ Reviewer
⏳ Finalizer
```

**Estado 2:**
```
🔍 *Reviewer* analisando... (2/3)
────────────────────
✅ Writer
⏳ Reviewer
⏳ Finalizer
```

**Estado 3:**
```
✨ *Finalizer* polindo... (3/3)
────────────────────
✅ Writer
✅ Reviewer
⏳ Finalizer
```

**Estado 4 (sucesso):**
```
✅ *Draft pronto*
────────────────────
✅ Writer
✅ Reviewer
✅ Finalizer
```

Em erro (qualquer fase):
```
❌ Erro em *Reviewer*
────────────────────
✅ Writer
❌ Reviewer
⏸ Finalizer

[detalhe curto do erro]
```

**Implementação:**
- Novo helper `execution/core/agents_progress.py`:
  ```python
  format_pipeline_progress(current_phase: str, phases_done: list[str], error: str | None = None) -> str
  ```
- `process_news_async` (em `app.py`) chama esse helper em 4 checkpoints: antes do Writer, antes do Reviewer, antes do Finalizer, ao concluir.
- Requer o pipeline dos 3 agents expor hooks — hoje `run_3_agents` é blocante e opaco. Duas opções:
  - (a) Callback hook: `run_3_agents(..., on_phase_start=lambda phase: ...)` — limpo mas precisa mudar assinatura.
  - (b) 3 chamadas separadas: `run_writer()`, `run_reviewer()`, `run_finalizer()` — melhor pro progresso mas refatora mais.
  - **Recomendado: (a)**. Callback é aditivo (default `None` = sem mudança de comportamento), roll-back trivial.

## Confirmações pós-click

Formato atual exemplo:
```
✅ *Arquivado* em 15:32 UTC
🆔 `17c1e97db96c`
```

Formato novo:
```
✅ *Arquivado*
🕒 15:32 UTC · 🆔 `17c1e97db96c`
```

- Meta (data + ID) em 1 linha só.
- **ID mantém-se completo** nos cards e confirmações — serve pra copiar quando o operador precisa usar `/reprocess <id>`. Truncamento só aparece em `/history` e `/rejections` (onde ID é só leitura).

Mesmo formato pra **Recusado**, **Enviado para o Writer**:
```
🖋️ *Enviado para o Writer*
🕒 15:32 UTC · 🆔 `17c1e97db96c`
```

Prompt de razão pós-Recusar:
```
❌ *Recusado*
🕒 15:32 UTC · 🆔 `17c1e97db96c`

💭 Por quê? (opcional — responda ou `pular`)
```

Confirmação de razão salva:
```
✅ Razão registrada.
   "não é iron ore, é coking coal"
```

## Testes

Mantém padrão TDD do projeto.

### Novos arquivos

**`tests/test_digest.py`** — fakeredis + formatter:
- `test_digest_single_type_news_only`
- `test_digest_single_type_rationale_only`
- `test_digest_mixed_types`
- `test_digest_preview_limits_to_3`
- `test_digest_shows_plus_n_when_over_3`
- `test_digest_escapes_markdown_in_titles`
- `test_digest_returns_none_on_zero`

**`tests/test_agents_progress.py`**:
- `test_progress_writer_active_phase_1`
- `test_progress_reviewer_active_phase_2`
- `test_progress_finalizer_active_phase_3`
- `test_progress_all_done`
- `test_progress_error_in_reviewer`

### Modificações em arquivos existentes

**`tests/test_query_handlers.py`** (atualiza assertions):
- Novo formato de `/stats` (emojis + separador + "No Writer").
- Novo formato de `/history` (ícones por tipo).
- Novo formato de `/rejections` (🕒 prefix).
- Novo formato de `/queue` (botões com título).

**`tests/test_curation_router.py`** (NOVO ou extendido):
- `test_route_items_stages_rationale_in_same_bucket`
- `test_route_items_returns_list_not_posts`
- `test_route_items_preserves_type_field`

**`tests/test_curation_telegram_poster.py`** (atualiza):
- Novo formato do card (título com ícone, meta 1 linha).

### Coverage mínima
- Manter 80%+ (padrão do projeto).
- Todos os novos helpers e formatters com happy path + edge case (None, vazio, caracteres especiais).

## Riscos e mitigações

| Risco | Mitigação |
|---|---|
| Quebrar o fluxo existente de rationale (script auto-processing que depende de timing) | Rationale-dispatcher fica no codebase mas deixa de ser chamado por router. Manual-call via script ainda funciona. |
| Editar assinatura de `run_3_agents` quebra callers | Hook `on_phase_start=None` é keyword-only default=None. Callers sem mudança = comportamento idêntico. |
| Digest pode dar false "0 novas" se dedup falhar | Dedup bug é escopo separado. Se `seen:<date>` deixar passar, digest conta como "novo" — pior caso = operador vê item repetido no queue (ruim mas já é o status quo). |
| Rename "3 Agents" → "Writer" em strings hardcoded esquecidas | Grep audit pré-merge: `grep -rn "3 [Aa]gents\|3_agents\|três agentes"` e auditar cada match. |
| Emojis não renderizam em alguns clientes Telegram | Fallback aceitável — Telegram hoje renderiza emojis em 100% dos clientes oficiais. Não é um cliente customizado. |
| Botão com título longo excede limite do Telegram (64 chars) | `_truncate(title, limit=40)` + ícone (2 chars) + espaço (1 char) = 43 chars ≤ 64. |

## Gotchas do ambiente

- Path do projeto com trailing space: `/Users/bigode/Dev/Antigravity WF `. Bash sempre com aspas.
- Dockerfile achata `webhook/` → `/app/` em produção. Imports em `webhook/*.py` devem ser bare (`import redis_queries`), não `from webhook import ...`. Conftest.py nos tests adiciona `webhook/` ao path.
- `execution/` é copiada inteira pra `/app/execution/` — imports `from execution.curation import ...` funcionam em prod.
- Telegram Markdown (legacy) requer escape de `\ _ * [ ] \``. O helper `_escape_md` de `telegram_poster` já cobre isso após a consolidação do commit `bbbb9cc`.
- `edit_message` no Telegram falha se o novo texto for idêntico ao atual — cuidado ao chamar 2x seguidas com mesmo estado.

## Dependências

Nenhuma nova lib Python. Usa:
- `redis`, `flask`, `requests`, `fakeredis` (já existentes).

## Entregáveis

1. `execution/curation/router.py` — modificado (annotate vira type-tag, remove post_for_curation do `_stage_and_post`).
2. `execution/scripts/platts_ingestion.py` (ou o caller do scrap) — envia digest no final se counters > 0.
3. `execution/core/agents_progress.py` — novo helper.
4. `webhook/digest.py` — novo formatter.
5. `webhook/query_handlers.py` — modificações nos 5 formatters (queue, stats, history, rejections, help opcional).
6. `webhook/app.py` — renames "3 Agents" → "Writer", progresso por fase, confirmações polidas.
7. `execution/curation/telegram_poster.py` — card polido (título com ícone no topo, meta em 1 linha, botões 2x2).
8. Testes: `tests/test_digest.py`, `tests/test_agents_progress.py` novos + atualizações em `tests/test_query_handlers.py`, `tests/test_curation_router.py`, `tests/test_curation_telegram_poster.py`.
9. Commits atômicos (seguindo padrão do projeto).
10. Follow-up tasks abertas:
    - **Investigar bug de duplicatas no scrap** (`platts:seen:<date>` deixando passar repetidos).
    - **Fase dedicada de ajuste de prompts dos 3 agents** (Writer/Reviewer/Finalizer) — incluindo lógica diferenciada pra rationale se o output atual mostrar problemas.
