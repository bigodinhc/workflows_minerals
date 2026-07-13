# Webhook Shared-Secret Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Proteger `POST /store-draft` e `GET /test-ai` do webhook Railway com um shared-secret no header `X-Webhook-Secret`, deletar o endpoint morto `/seen-articles`, e fazer os clientes (crons GH Actions) enviarem o header.

**Architecture:** Helper síncrono `require_shared_secret(request)` em `webhook/routes/api.py` lê `WEBHOOK_SHARED_SECRET` do env em tempo de chamada e compara com `hmac.compare_digest` (fail-closed: env ausente → 500; header errado/ausente → 401). Os clientes (`channel_publisher.py`, `rationale_dispatcher.py`) adicionam o header lido do mesmo env; os workflows do GH Actions expõem o secret via `secrets.WEBHOOK_SHARED_SECRET`.

**Tech Stack:** Python 3.11, aiohttp (webhook), requests (clientes), pytest + pytest-asyncio, GH Actions YAML.

**Spec:** `docs/superpowers/specs/2026-07-10-webhook-shared-secret-design.md`

## Global Constraints

- Header exato: `X-Webhook-Secret`. Env exato (servidor E clientes): `WEBHOOK_SHARED_SECRET`.
- Env é lido **em tempo de chamada** (nunca em import/módulo-level) — padrão do projeto.
- Fail-closed no servidor: env ausente/vazio → `500 {"error": "WEBHOOK_SHARED_SECRET not configured"}` + `logger.critical`; header ausente/errado → `401 {"error": "unauthorized"}`. Sem flag de desligar.
- Comparação com `hmac.compare_digest` sobre bytes (`.encode()` nos dois lados — header pode conter não-ASCII e `compare_digest` sobre `str` levantaria TypeError).
- `/health`, `/metrics`, `/admin/register-commands`, rotas OneDrive e webhook do Telegram **não mudam**.
- Rodar testes com `.venv/bin/python -m pytest` a partir da raiz do repo.
- Commits em português, formato `<type>: <descrição>`, sem attribution.

---

### Task 1: Gate de shared-secret no servidor (`/store-draft` e `/test-ai`)

**Files:**
- Modify: `webhook/routes/api.py` (imports; helper novo; primeira linha de `store_draft` e `test_ai`)
- Create: `tests/test_webhook_shared_secret.py`
- Modify: `tests/test_store_draft_delivery_routing.py` (fixture autouse + headers no `_FakeRequest`)

**Interfaces:**
- Produces: `require_shared_secret(request: web.Request) -> web.Response | None` em `routes.api` — `None` = autorizado, `web.Response` = erro pronto pra retornar. Task 3 depende do contrato header/env; nenhuma outra task importa o helper.

**Contexto:** os testes deste repo chamam os handlers aiohttp diretamente com um fake request (sem test server) — ver `tests/test_store_draft_delivery_routing.py`. O fake precisa ganhar atributo `.headers` porque o helper o consulta ANTES de `await request.json()`.

- [ ] **Step 1: Write the failing tests**

Criar `tests/test_webhook_shared_secret.py`:

```python
"""Gate de shared-secret nos endpoints HTTP do webhook."""
import sys
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

import pytest

SECRET = "s3gr3d0-de-teste"


class _FakeRequest:
    def __init__(self, payload: dict | None = None, headers: dict | None = None):
        self._payload = payload or {}
        self.headers = headers or {}

    async def json(self):
        return self._payload


@pytest.fixture
def secret_env(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SHARED_SECRET", SECRET)


def test_helper_fail_closed_sem_env(monkeypatch):
    monkeypatch.delenv("WEBHOOK_SHARED_SECRET", raising=False)
    from routes.api import require_shared_secret
    resp = require_shared_secret(_FakeRequest(headers={"X-Webhook-Secret": "qualquer"}))
    assert resp is not None
    assert resp.status == 500


def test_helper_rejeita_header_ausente(secret_env):
    from routes.api import require_shared_secret
    resp = require_shared_secret(_FakeRequest())
    assert resp is not None
    assert resp.status == 401


def test_helper_rejeita_header_errado(secret_env):
    from routes.api import require_shared_secret
    resp = require_shared_secret(_FakeRequest(headers={"X-Webhook-Secret": "errado"}))
    assert resp is not None
    assert resp.status == 401


def test_helper_aceita_header_correto(secret_env):
    from routes.api import require_shared_secret
    resp = require_shared_secret(_FakeRequest(headers={"X-Webhook-Secret": SECRET}))
    assert resp is None


@pytest.mark.asyncio
async def test_store_draft_sem_header_401_e_nada_persistido(secret_env):
    from routes.api import store_draft
    with patch("routes.api.drafts_set") as drafts:
        resp = await store_draft(
            _FakeRequest({"draft_id": "d1", "message": "conteúdo"})
        )
    assert resp.status == 401
    drafts.assert_not_called()


@pytest.mark.asyncio
async def test_test_ai_sem_header_401(secret_env):
    from routes.api import test_ai
    resp = await test_ai(_FakeRequest())
    assert resp.status == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_webhook_shared_secret.py -v`
Expected: FAIL — `ImportError: cannot import name 'require_shared_secret'` (todos os testes).

- [ ] **Step 3: Implement the gate in `webhook/routes/api.py`**

3a. Adicionar aos imports do topo (depois de `import logging`):

```python
import hmac
import os
```

3b. Adicionar o helper logo depois de `routes = web.RouteTableDef()`:

```python
def require_shared_secret(request: web.Request) -> web.Response | None:
    """None = autorizado; web.Response = erro pronto pra retornar.

    Lê WEBHOOK_SHARED_SECRET em tempo de chamada (env muda sem redeploy
    de código). Fail-closed: sem secret configurado, nada passa.
    """
    expected = os.getenv("WEBHOOK_SHARED_SECRET", "").strip()
    if not expected:
        logger.critical("WEBHOOK_SHARED_SECRET not configured — rejecting request")
        return web.json_response(
            {"error": "WEBHOOK_SHARED_SECRET not configured"}, status=500
        )
    provided = request.headers.get("X-Webhook-Secret", "")
    if not hmac.compare_digest(provided.encode(), expected.encode()):
        return web.json_response({"error": "unauthorized"}, status=401)
    return None
```

3c. Primeira linha do corpo de `store_draft` (antes de `data = await request.json()`):

```python
    if (denied := require_shared_secret(request)) is not None:
        return denied
```

3d. Primeira linha do corpo de `test_ai` (antes do check de `ANTHROPIC_API_KEY`):

```python
    if (denied := require_shared_secret(request)) is not None:
        return denied
```

- [ ] **Step 4: Fix the existing store-draft routing tests**

Em `tests/test_store_draft_delivery_routing.py` (que chama `store_draft` direto e agora tomaria 500/401):

4a. Adicionar constante e fixture autouse depois de `import pytest`:

```python
SECRET = "s3gr3d0-de-teste"


@pytest.fixture(autouse=True)
def _shared_secret_env(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SHARED_SECRET", SECRET)
```

4b. `_FakeRequest` passa a carregar o header válido:

```python
class _FakeRequest:
    def __init__(self, payload: dict):
        self._payload = payload
        self.headers = {"X-Webhook-Secret": SECRET}

    async def json(self):
        return self._payload
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_webhook_shared_secret.py tests/test_store_draft_delivery_routing.py -v`
Expected: PASS (6 novos + 3 existentes).

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: tudo verde (nenhum outro teste chama `store_draft`/`test_ai`).

- [ ] **Step 7: Commit**

```bash
git add webhook/routes/api.py tests/test_webhook_shared_secret.py tests/test_store_draft_delivery_routing.py
git commit -m "feat(webhook): shared-secret X-Webhook-Secret no /store-draft e /test-ai"
```

---

### Task 2: Deletar o endpoint morto `/seen-articles`

**Files:**
- Modify: `webhook/routes/api.py`

**Interfaces:**
- Consumes: nada. Produces: nada (só remoção). Nenhum chamador existe no repo (verificado no brainstorm: grep repo-wide só acha as próprias rotas).

- [ ] **Step 1: Remove as rotas e o estado**

Em `webhook/routes/api.py`, deletar:

1. As funções `get_seen_articles` (rota `GET /seen-articles`) e `store_seen_articles` (rota `POST /seen-articles`) inteiras, com seus decorators.
2. O dict módulo-level `SEEN_ARTICLES: dict = {}` e o comentário acima dele (`# In-memory state for seen articles ...`).
3. A linha `"seen_articles_dates": len(SEEN_ARTICLES),` da resposta do `/health`.
4. O import `from datetime import datetime, timedelta` (usado só pela poda do seen-articles — confirmar com grep antes de remover: `grep -n "datetime\|timedelta" webhook/routes/api.py`).
5. A linha `- GET/POST /seen-articles (GitHub Actions -> dedup for market_news)` do docstring do módulo.

- [ ] **Step 2: Verify nothing references the removed code**

Run: `grep -rn "seen_articles\|SEEN_ARTICLES\|seen-articles" webhook/ tests/ execution/ --include="*.py" | grep -v __pycache__`
Expected: saída vazia.

- [ ] **Step 3: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: tudo verde.

- [ ] **Step 4: Commit**

```bash
git add webhook/routes/api.py
git commit -m "refactor(webhook): remove endpoint morto /seen-articles"
```

---

### Task 3: Clientes enviam o header `X-Webhook-Secret`

**Files:**
- Modify: `execution/integrations/channel_publisher.py` (POST ganha `headers=`)
- Modify: `execution/curation/rationale_dispatcher.py` (POST ganha `headers=`)
- Test: `tests/test_channel_publisher.py` (ajustar teste existente + 1 novo)

**Interfaces:**
- Consumes: contrato do servidor da Task 1 — header `X-Webhook-Secret`, env `WEBHOOK_SHARED_SECRET`.
- Produces: nada consumido por outras tasks.

**Contexto:** `rationale_dispatcher.py` é módulo ÓRFÃO (docstring do próprio arquivo: o router não o chama mais; mantido como utilitário manual). A mudança lá é 1 linha mecânica, sem testes novos — o investimento em teste fica no `channel_publisher`, que é o caminho crítico dos 3 crons.

- [ ] **Step 1: Update the failing test**

Em `tests/test_channel_publisher.py`, no teste existente `test_publish_posts_store_draft`, adicionar o env e o assert do header:

```python
def test_publish_posts_store_draft(monkeypatch):
    monkeypatch.setenv("WEBHOOK_BASE_URL", "https://example.up.railway.app/")
    monkeypatch.setenv("WEBHOOK_SHARED_SECRET", "s3gr3d0")
    from execution.integrations import channel_publisher as cp
    with patch.object(cp.requests, "post", return_value=_ok_response()) as post:
        result = cp.publish_to_channel("daily_report", "corpo *msg*", "draft-42")
    assert result == {"ok": True, "message_id": 9, "error": None}
    args, kwargs = post.call_args
    assert args[0] == "https://example.up.railway.app/store-draft"  # trailing / stripped
    assert kwargs["json"] == {
        "draft_id": "draft-42",
        "message": "corpo *msg*",
        "workflow_type": "daily_report",
        "direct_delivery": True,
    }
    assert kwargs["headers"] == {"X-Webhook-Secret": "s3gr3d0"}
    assert kwargs["timeout"] == 90
```

E adicionar novo teste no fim do arquivo (documenta o comportamento com env ausente: header vazio sai mesmo assim e o servidor responde 401 → job falha vermelho):

```python
def test_publish_sem_secret_manda_header_vazio(monkeypatch):
    monkeypatch.setenv("WEBHOOK_BASE_URL", "https://example.up.railway.app")
    monkeypatch.delenv("WEBHOOK_SHARED_SECRET", raising=False)
    from execution.integrations import channel_publisher as cp
    with patch.object(cp.requests, "post", return_value=_ok_response()) as post:
        cp.publish_to_channel("daily_report", "corpo", "draft-43")
    assert post.call_args.kwargs["headers"] == {"X-Webhook-Secret": ""}
```

- [ ] **Step 2: Run tests to verify the updated/new ones fail**

Run: `.venv/bin/python -m pytest tests/test_channel_publisher.py -v`
Expected: FAIL — `KeyError: 'headers'` nos dois testes acima; demais passam.

- [ ] **Step 3: Add the header in `channel_publisher.py`**

No `requests.post` de `publish_to_channel`, adicionar o kwarg `headers` (entre `json=` e `timeout=`):

```python
        resp = requests.post(
            f"{base_url}/store-draft",
            json={
                "draft_id": draft_id,
                "message": message,
                "workflow_type": workflow_type,
                "direct_delivery": True,
            },
            headers={"X-Webhook-Secret": os.getenv("WEBHOOK_SHARED_SECRET", "")},
            timeout=_TIMEOUT_SECONDS,
        )
```

- [ ] **Step 4: Add the header in `rationale_dispatcher.py`**

No `requests.post` dentro de `process()` (bloco `if webhook_url:`), adicionar o mesmo kwarg entre `json={...}` e `timeout=10`:

```python
                headers={"X-Webhook-Secret": os.getenv("WEBHOOK_SHARED_SECRET", "")},
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_channel_publisher.py -v`
Expected: PASS (todos).

- [ ] **Step 6: Commit**

```bash
git add execution/integrations/channel_publisher.py execution/curation/rationale_dispatcher.py tests/test_channel_publisher.py
git commit -m "feat(crons): clientes do /store-draft enviam header X-Webhook-Secret"
```

---

### Task 4: Secret no env dos workflows do GH Actions

**Files:**
- Modify: `.github/workflows/daily_report.yml`
- Modify: `.github/workflows/morning_check.yml`
- Modify: `.github/workflows/baltic_ingestion.yml`
- Modify: `.github/workflows/market_news.yml`

**Interfaces:**
- Consumes: env `WEBHOOK_SHARED_SECRET` esperado pelos clientes da Task 3.
- Produces: nada.

**Contexto:** cada workflow tem um bloco `env:` no step principal (ex.: `daily_report.yml` linha ~35). Adicionar UMA linha em cada, junto das outras vars de webhook (`WEBHOOK_BASE_URL`/`TELEGRAM_WEBHOOK_URL`/`CLIENT_DELIVERY_CHANNEL`), preservando a indentação existente do bloco:

```yaml
          WEBHOOK_SHARED_SECRET: ${{ secrets.WEBHOOK_SHARED_SECRET }}
```

- [ ] **Step 1: Add the line to the 4 workflows**

Nos 4 arquivos, dentro do bloco `env:` do step que roda o script Python.

- [ ] **Step 2: Verify**

Run: `grep -c "WEBHOOK_SHARED_SECRET: \${{ secrets.WEBHOOK_SHARED_SECRET }}" .github/workflows/daily_report.yml .github/workflows/morning_check.yml .github/workflows/baltic_ingestion.yml .github/workflows/market_news.yml`
Expected: `1` em cada arquivo.

Run: `.venv/bin/python -c "import yaml,glob; [yaml.safe_load(open(f)) for f in glob.glob('.github/workflows/*.yml')]; print('yaml ok')"`
Expected: `yaml ok`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/daily_report.yml .github/workflows/morning_check.yml .github/workflows/baltic_ingestion.yml .github/workflows/market_news.yml
git commit -m "ci: expõe WEBHOOK_SHARED_SECRET nos workflows que falam com o webhook"
```

---

## Rollout (manual, pós-merge — NÃO é task de subagente)

Ordem importa (fail-closed — o secret tem que existir nos dois lados ANTES do deploy):

1. `openssl rand -hex 32` → gerar o secret.
2. `gh secret set WEBHOOK_SHARED_SECRET --body "<secret>"` (repo) e
   `railway variables --set "WEBHOOK_SHARED_SECRET=<secret>"` no service web (projeto keen-stillness).
   Sem whitespace/newline no valor (o servidor faz strip do env; os clientes enviam cru).
3. Merge do PR → deploy Railway (confirmar que o container novo subiu; se necessário `railway redeploy --yes`).
4. Smoke: `curl -s -o /dev/null -w "%{http_code}" -X POST <base>/store-draft -H 'Content-Type: application/json' -d '{"draft_id":"smoke","message":"x"}'` → espera `401`; repetir com `-H "X-Webhook-Secret: <secret>"` → espera `200`.
5. Validação final no próximo cron real (GH Actions verde + post no canal).
