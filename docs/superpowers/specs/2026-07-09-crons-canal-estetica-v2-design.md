# Design: Crons → canal Telegram (fecha vazamento uazapi) + estética v2 do post

- **Data:** 2026-07-09
- **Status:** Aprovado (design); aguardando plano de implementação
- **Autor:** brainstorming colaborativo (usuário + Claude)
- **Contexto:** continuação de `2026-07-09-telegram-channel-delivery-design.md` (PR #3, merged)

## 1. Problema

A migração do PR #3 gateou 3 pontos de entrega (`/store-draft`, `process_approval_async`,
`dispatch_document`) — mas **os 3 crons diários não passam por nenhum deles**. Os scripts
`send_daily_report.py`, `morning_check.py` e `baltic_ingestion.py` (GH Actions) chamam
`uazapi.send_message` **diretamente** via `DeliveryReporter`, broadcast pra lista
`minerals_report` (~74 clientes). Ou seja: mesmo pós-merge, os crons continuam disparando
WhatsApp pro chip restrito todo dia.

Descoberta adicional que corrige o design anterior: `morning_check` e `baltic_ingestion`
foram classificados como "internos" no spec do PR #3, mas o código prova que **são conteúdo
de cliente** (broadcast pros 74 via WhatsApp desde sempre).

### Decisões travadas (brainstorming 2026-07-09)

1. **Tudo pro canal**: `daily_report`, `morning_check` e `baltic_ingestion` passam a postar
   no canal privado — o cliente segue recebendo o que recebia, só que no Telegram
   (3-4 posts/dia).
2. **Estética v2 = polish do texto apenas**: citação expansível pra posts longos.
   **Sem botão inline** (entra quando o fluxo de atendimento estiver definido) e **sem
   card-imagem** (fase futura, requer identidade visual definida).
3. **Corte do expansível**: estrutural + trava de tamanho (detalhe em §3).

## 2. Fase 1 — Crons → canal (urgente)

### 2.1 Mecanismo

Cada script troca o fan-out uazapi por **1 POST no `/store-draft` do webhook Railway** com
`direct_delivery: true` e seu `workflow_type`. Reusa tudo que já existe e está testado:
roteamento → `post_report_to_channel` → conversão `*negrito*`→HTML → retry de flood-wait →
never-raise. A resposta JSON (`telegram_delivery`) vira o resultado do envio no script.

- **Gate script-side**: helper compartilhado lê `CLIENT_DELIVERY_CHANNEL` (default
  `telegram` → POST no webhook, pula uazapi; `uazapi` → caminho legado `DeliveryReporter`
  intacto). Rollback simétrico ao resto do sistema, via env do GH Actions.
- **URL do webhook**: env `WEBHOOK_BASE_URL` nos workflows do GH Actions (novo secret/var;
  valor atual `https://web-production-0d909.up.railway.app`).
- **Helper compartilhado** (novo, `execution/integrations/channel_publisher.py`):
  `publish_to_channel(workflow_type, message, draft_id) -> dict` — POST com timeout,
  retorna o dict `telegram_delivery`; nunca levanta (erro → dict com `ok: False`).
  Os 3 scripts chamam o mesmo helper (DRY).

### 2.2 Roteamento

`CLIENT_WORKFLOWS` em `webhook/bot/routing.py` ganha `morning_check` e `baltic_ingestion`
(os 5 workflows viram canal). O DM broadcast interno (`deliver_to_subscribers`) permanece
como caminho para workflows desconhecidos/futuros — na prática deixa de ser usado pelos
fluxos atuais.

### 2.3 O que NÃO muda nos scripts

- Idempotência própria (`daily_report:sent:<tipo>:<dia>` etc. no Redis) — trava anti-duplo
  envio continua antes do POST.
- `ProgressReporter`/`EventBus` — cards de progresso no chat admin intactos; só o label da
  etapa final muda (ex.: "Publicando no canal" em vez de "Enviando WhatsApp para N contatos").
- `--dry-run` continua imprimindo preview sem enviar.
- Código uazapi permanece nos scripts (branch do gate), como rollback.

### 2.4 Tratamento de erros

- POST falhou (rede/timeout/HTTP≠200) → helper retorna `{"ok": False, "error": ...}`;
  script loga, emite evento `channel_publish_failed` e **falha o job** (GH Actions marca
  vermelho → alerta existente no chat admin). Sem fallback automático pro uazapi — falha
  visível é melhor que broadcast fantasma no chip restrito.
- `telegram_delivery.ok == false` na resposta → mesmo tratamento.

### 2.5 Alternativas descartadas

- Postar direto na Bot API de dentro do GH Actions: duplicaria a conversão de formato e o
  flood-retry em outro runtime.
- Mover os crons pra dentro do bot (scheduler): reestrutura grande sem necessidade (YAGNI).

## 3. Fase 2 — Estética v2: citação expansível

Validada contra os templates reais (Curator v4: header 4 linhas → lead em prosa com
número-chave → seções `*CAPS*` → `Watch:`; e formatos script-built do daily_report/
morning_check/baltic).

### 3.1 Regra de corte (estrutural + trava de tamanho)

Nova função pura em `webhook/bot/channel_delivery.py`:
`split_for_expandable(text: str, threshold: int = 900) -> tuple[str, str | None]`

- `len(text) <= threshold` → `(text, None)`: post passa intacto (daily_report do script,
  ~600-800 chars, cai aqui — correto, já é compacto).
- Post longo → divide o texto **cru** em blocos por linha em branco (`\n\n`):
  - **Visível**: bloco 0 (header do Curator) + bloco 1 (lead em prosa, carrega o
    número-chave).
  - **Recolhido**: todos os blocos restantes (seções, bullets, Watch:), embrulhados em
    `<blockquote expandable>`.
- **< 3 blocos** → sem divisão (post malformado/sem estrutura nunca quebra).
- Split acontece **após** o truncamento de `RAW_TEXT_LIMIT` e **antes** da conversão;
  cada parte é convertida separadamente com `to_telegram_html` (fronteira de bloco →
  nenhum par `*...*` é cortado ao meio).
- Falha em qualquer ponto do split → degrada pro comportamento atual (post inteiro),
  dentro do try never-raise existente.

### 3.2 Fora do escopo da fase 2 (anotado como futuro)

Botão inline (precisa do destino de atendimento definido); card-imagem com identidade
visual (send_photo + template PNG); normalização de header entre workflows.

## 4. Testes

- **Fase 1**: unit do helper `publish_to_channel` (POST correto, timeout, HTTP 500,
  `ok: false` na resposta, gate uazapi não posta); unit por script do branch telegram
  (POST chamado com workflow_type certo, uazapi não tocado) e do branch uazapi (legado
  intacto) — espelha o padrão de fixture `CLIENT_DELIVERY_CHANNEL` dos testes do PR #3;
  roteamento atualizado (5 workflows → canal).
- **Fase 2**: unit do `split_for_expandable` (curto intacto; longo divide em bloco 0+1
  visível; <3 blocos intacto; threshold exato; conversão dentro do blockquote; par de
  negrito nunca cortado); integração `post_report_to_channel` com post longo (blockquote
  presente no texto enviado).
- **Smoke real**: 1 post longo estilo Curator no canal oficial pra validação visual do
  expansível antes do push (padrão da sessão de hoje).

## 5. Rollout

1. Merge fase 1 → secret/var `WEBHOOK_BASE_URL` nos 3 workflows do GH Actions → próximo
   cron já posta no canal (validar no primeiro disparo real).
2. Fase 2 é só deploy do webhook (Railway) — sem mudança nos crons.
3. Rollback: `CLIENT_DELIVERY_CHANNEL=uazapi` no GH Actions (fase 1) e/ou Railway (resto).
