# Design: Shared-secret nos endpoints HTTP do webhook

- **Data:** 2026-07-10
- **Status:** Aprovado (aguardando plano de implementação)
- **Autor:** brainstorming colaborativo (usuário + Claude)
- **Contexto:** follow-up da migração Telegram (PRs #3 e #4). O `/store-draft` virou o
  caminho primário de conteúdo de cliente pro canal — e está aberto.

## 1. Problema

O webhook Railway (`https://web-production-0d909.up.railway.app`) expõe endpoints HTTP
sem autenticação:

- **`POST /store-draft`** — qualquer um com a URL posta mensagem direto no canal privado
  dos clientes (com `direct_delivery: true` + `workflow_type` de cliente). Também aceita
  `uazapi_token` arbitrário no body.
- **`GET /test-ai`** — cada hit chama a API da Anthropic (queima crédito).
- **`GET/POST /seen-articles`** — endpoint **morto**: nenhum chamador no repo (o dedup do
  market_news não passa mais por ele). Superfície de ataque gratuita.

Chamadores legítimos do `/store-draft` hoje:

1. `execution/integrations/channel_publisher.py` — crons daily_report, morning_check e
   baltic_ingestion (GH Actions), env `WEBHOOK_BASE_URL`, `direct_delivery: true`.
   **Caminho crítico.**
2. `execution/curation/rationale_dispatcher.py` — market_news (GH Actions), env
   `TELEGRAM_WEBHOOK_URL`, `direct_delivery: false`, best-effort (falha vira warning).

### Decisões travadas (brainstorming 2026-07-10)

1. **Escopo:** proteger `/store-draft` e `/test-ai` com o mesmo secret; **deletar**
   `/seen-articles` (morto — se precisar, volta do git). `/health` e `/metrics` seguem
   abertos (conteúdo agregado/não-sensível).
2. **Mecanismo:** helper explícito por handler (abordagem A) — não middleware. Com 2
   handlers protegidos, middleware adicionaria ordenação e lista de exclusões (webhook
   Telegram, OneDrive, mini-app) sem ganho.
3. **Fail-closed:** secret ausente no servidor → rejeita com erro claro. Mesma filosofia
   do spec anterior ("falha visível é melhor que broadcast fantasma"). Sem flag de
   desligar — segurança não ganha modo off; rollback é revert do deploy.

## 2. Servidor (`webhook/routes/api.py`)

Helper novo no próprio `api.py`:

```python
def require_shared_secret(request: web.Request) -> web.Response | None:
    """None = autorizado; Response = erro pronto pra retornar.

    Lê WEBHOOK_SHARED_SECRET em tempo de chamada (padrão do projeto: env
    muda sem redeploy de código). Compara com hmac.compare_digest.
    """
```

Comportamento:

| Condição | Resposta |
|---|---|
| `WEBHOOK_SHARED_SECRET` ausente/vazio no env do servidor | `500 {"error": "WEBHOOK_SHARED_SECRET not configured"}` + `logger.critical` |
| Header `X-Webhook-Secret` ausente ou diferente | `401 {"error": "unauthorized"}` |
| Header confere (`hmac.compare_digest`) | `None` (handler segue) |

Aplicação: primeira linha de `store_draft` e `test_ai` —
`if (denied := require_shared_secret(request)) is not None: return denied`.

Remoções:

- Rotas `GET /seen-articles` e `POST /seen-articles`, o dict módulo-level
  `SEEN_ARTICLES` e o campo `seen_articles_dates` da resposta do `/health`.

Não muda: `/health`, `/metrics`, `/admin/register-commands` (já valida chat_id),
rotas OneDrive (já validam `GRAPH_WEBHOOK_CLIENT_STATE`), webhook do Telegram
(secret path do aiogram), mini-app.

## 3. Clientes

- **`execution/integrations/channel_publisher.py`**: adiciona
  `headers={"X-Webhook-Secret": os.getenv("WEBHOOK_SHARED_SECRET", "")}` no POST.
  Env ausente no cron → servidor responde 401 → helper retorna `{"ok": False, ...}` →
  o script falha o job (comportamento existente, vermelho visível no GH Actions).
- **`execution/curation/rationale_dispatcher.py`**: mesmo header no POST do
  `/store-draft`. Caminho continua best-effort (falha vira `log.warning`, aprovação
  via dashboard segue funcionando).
- **GH Actions**: adicionar `WEBHOOK_SHARED_SECRET: ${{ secrets.WEBHOOK_SHARED_SECRET }}`
  no bloco `env` dos 4 workflows: `daily_report.yml`, `morning_check.yml`,
  `baltic_ingestion.yml`, `market_news.yml`.

## 4. Rollout (ordem importa — fail-closed)

1. Gerar secret: `openssl rand -hex 32`.
2. Setar ANTES do deploy: `gh secret set WEBHOOK_SHARED_SECRET` e
   `railway variables --set "WEBHOOK_SHARED_SECRET=..."` (service web / keen-stillness).
3. Merge + deploy Railway.
4. Smoke: `curl -X POST .../store-draft` sem header → espera 401; com header e payload
   dummy sem `direct_delivery` → espera 200. Validação final no próximo cron real.

**Rollback:** revert do deploy. Não existe flag pra desligar a checagem.

## 5. Testes

- Unit do helper: env ausente → 500; header ausente → 401; header errado → 401;
  header correto → None.
- Endpoint `store_draft`: sem header → 401 e **nenhum draft salvo / nenhuma entrega**;
  com header → fluxo normal.
- Endpoint `test_ai`: sem header → 401.
- Testes existentes de `/store-draft` (roteamento de entrega): fixture autouse setando
  `WEBHOOK_SHARED_SECRET` e injetando o header nos requests de teste.
- `channel_publisher`: unit verificando que o POST sai com o header `X-Webhook-Secret`
  vindo do env (mock de `requests.post`).
- Rotas `/seen-articles` removidas: testes que as cobrem (se existirem) são removidos.
