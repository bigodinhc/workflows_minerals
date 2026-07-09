# Crons → Canal Telegram + Estética v2 — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fechar o vazamento uazapi dos 3 crons diários (passam a publicar no canal Telegram via `/store-draft`) e adicionar a citação expansível pra posts longos.

**Architecture:** Novo helper `execution/integrations/channel_publisher.py` faz 1 POST no `/store-draft` do webhook Railway (que já roteia pro canal com conversão HTML e flood-retry). Cada script extrai sua seção de envio pra uma função `deliver_message()` testável, gateada por `CLIENT_DELIVERY_CHANNEL` (telegram default | uazapi rollback). `CLIENT_WORKFLOWS` passa a ter os 5 workflows. Fase 2: `split_for_expandable()` no `channel_delivery` colapsa seções de posts longos em `<blockquote expandable>`.

**Tech Stack:** Python 3, requests (GH Actions side), aiogram 3 (webhook side), pytest + pytest-asyncio + unittest.mock.

**Spec:** `docs/superpowers/specs/2026-07-09-crons-canal-estetica-v2-design.md`

## Global Constraints

- Branch de trabalho: `feat/crons-to-channel` (já ativa, base = main pós-PR #3).
- Testes: `.venv/bin/python -m pytest tests/<arquivo> -v` (venv da raiz). Async tests usam `@pytest.mark.asyncio`.
- Working tree tem arquivos sujos não relacionados (`actors/platts-scrap-full-news/*`, `webhook/uv.lock`, `"[WF] RELATORIO DIARIO.json"`). **Nunca `git add -A`** — stage só os arquivos da task.
- `delivery_mode()` do helper é ESPELHO de `webhook/bot/routing.client_delivery_mode` (mesma semântica: default `telegram`, só `uazapi` explícito muda; lê env em tempo de chamada). Padrão de espelho já existe no repo (`dispatch_document._broadcast_delay_range`).
- Env novas (GH Actions): `WEBHOOK_BASE_URL` (valor: `https://web-production-0d909.up.railway.app`), `CLIENT_DELIVERY_CHANNEL` (rollback).
- Falha de publicação no canal **derruba o job** (raise → GH Actions vermelho → alerta existente). Sem fallback automático pro uazapi.
- Código uazapi permanece nos scripts (branch legado dentro de `deliver_message`), **movido verbatim** — é o rollback.
- Idempotência dos scripts (`sent_key` no Redis) preservada: flag só é setada quando `deliver_message` retorna True.
- Fase 2: split acontece no texto CRU, após truncamento `RAW_TEXT_LIMIT` e antes da conversão; falha no split degrada pro post inteiro (nunca bloqueia).
- Commits convencionais em PT, sem attribution.

---

### Task 1: Helper `channel_publisher`

**Files:**
- Create: `execution/integrations/channel_publisher.py`
- Test: `tests/test_channel_publisher.py`

**Interfaces:**
- Consumes: endpoint `POST /store-draft` do webhook (payload `{draft_id, message, workflow_type, direct_delivery: true}`, resposta `{"success": true, "telegram_delivery": {"ok", "message_id", "error"}}`).
- Produces (Tasks 3-5 dependem):
  - `delivery_mode() -> str` — `"telegram"` (default) ou `"uazapi"`
  - `publish_to_channel(workflow_type: str, message: str, draft_id: str) -> dict` — retorna o dict `telegram_delivery` do webhook, ou `{"ok": False, "message_id": None, "error": str}` em qualquer problema de transporte. **Nunca levanta.**

- [ ] **Step 1: Confirmar que `requests` está no requirements da raiz (GH Actions)**

Run: `grep -c '^requests' requirements.txt`
Expected: `1` (se `0`, adicionar `requests>=2.28.0` ao `requirements.txt` da raiz e incluir no commit).

- [ ] **Step 2: Escrever os testes que falham**

Criar `tests/test_channel_publisher.py`:

```python
"""Tests for the GH Actions → webhook channel publisher."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

import pytest


def test_delivery_mode_defaults_to_telegram(monkeypatch):
    monkeypatch.delenv("CLIENT_DELIVERY_CHANNEL", raising=False)
    from execution.integrations.channel_publisher import delivery_mode
    assert delivery_mode() == "telegram"


def test_delivery_mode_uazapi(monkeypatch):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "uazapi")
    from execution.integrations.channel_publisher import delivery_mode
    assert delivery_mode() == "uazapi"


def test_delivery_mode_garbage_falls_back(monkeypatch):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "pombo-correio")
    from execution.integrations.channel_publisher import delivery_mode
    assert delivery_mode() == "telegram"


def _ok_response():
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "success": True,
        "draft_id": "d1",
        "telegram_delivery": {"ok": True, "message_id": 9, "error": None},
    }
    return resp


def test_publish_posts_store_draft(monkeypatch):
    monkeypatch.setenv("WEBHOOK_BASE_URL", "https://example.up.railway.app/")
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
    assert kwargs["timeout"] == 30


def test_publish_without_base_url(monkeypatch):
    monkeypatch.delenv("WEBHOOK_BASE_URL", raising=False)
    from execution.integrations import channel_publisher as cp
    with patch.object(cp.requests, "post") as post:
        result = cp.publish_to_channel("daily_report", "m", "d")
    post.assert_not_called()
    assert result["ok"] is False
    assert "WEBHOOK_BASE_URL" in result["error"]


def test_publish_http_error(monkeypatch):
    monkeypatch.setenv("WEBHOOK_BASE_URL", "https://x.test")
    resp = MagicMock()
    resp.status_code = 502
    resp.text = "bad gateway"
    from execution.integrations import channel_publisher as cp
    with patch.object(cp.requests, "post", return_value=resp):
        result = cp.publish_to_channel("daily_report", "m", "d")
    assert result["ok"] is False
    assert "502" in result["error"]


def test_publish_network_exception(monkeypatch):
    monkeypatch.setenv("WEBHOOK_BASE_URL", "https://x.test")
    from execution.integrations import channel_publisher as cp
    with patch.object(cp.requests, "post", side_effect=OSError("timeout")):
        result = cp.publish_to_channel("daily_report", "m", "d")
    assert result["ok"] is False
    assert "timeout" in result["error"]


def test_publish_response_without_delivery(monkeypatch):
    monkeypatch.setenv("WEBHOOK_BASE_URL", "https://x.test")
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"success": True, "draft_id": "d"}
    from execution.integrations import channel_publisher as cp
    with patch.object(cp.requests, "post", return_value=resp):
        result = cp.publish_to_channel("daily_report", "m", "d")
    assert result["ok"] is False
    assert "telegram_delivery" in result["error"]
```

- [ ] **Step 3: Rodar e confirmar falha**

Run: `.venv/bin/python -m pytest tests/test_channel_publisher.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'execution.integrations.channel_publisher'`

- [ ] **Step 4: Implementar**

Criar `execution/integrations/channel_publisher.py`:

```python
"""Publish client-workflow messages to the Telegram channel via the webhook.

GH Actions scripts can't import webhook/bot/* (aiogram is not in the root
requirements), so they publish through the deployed webhook's /store-draft
endpoint, which owns routing, HTML conversion and flood-wait retry.
"""

from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 30


def delivery_mode() -> str:
    """'telegram' (default) or 'uazapi' (legacy rollback).

    Mirror of webhook/bot/routing.client_delivery_mode — keep in sync.
    Read at call time so GH Actions env changes apply without code changes.
    """
    mode = os.getenv("CLIENT_DELIVERY_CHANNEL", "telegram").strip().lower()
    return "uazapi" if mode == "uazapi" else "telegram"


def publish_to_channel(workflow_type: str, message: str, draft_id: str) -> dict:
    """POST the message to {WEBHOOK_BASE_URL}/store-draft with direct_delivery.

    Returns the webhook's telegram_delivery dict ({"ok", "message_id",
    "error"}), or {"ok": False, ...} on any transport problem. Never raises —
    callers decide whether a failed publish fails the job.
    """
    base_url = os.getenv("WEBHOOK_BASE_URL", "").strip().rstrip("/")
    if not base_url:
        logger.error("WEBHOOK_BASE_URL not set — channel publish skipped")
        return {"ok": False, "message_id": None, "error": "WEBHOOK_BASE_URL not set"}

    try:
        resp = requests.post(
            f"{base_url}/store-draft",
            json={
                "draft_id": draft_id,
                "message": message,
                "workflow_type": workflow_type,
                "direct_delivery": True,
            },
            timeout=_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.error(f"channel publish request failed: {exc}")
        return {"ok": False, "message_id": None, "error": str(exc)[:300]}

    if resp.status_code != 200:
        logger.error(f"channel publish HTTP {resp.status_code}: {resp.text[:200]}")
        return {"ok": False, "message_id": None, "error": f"HTTP {resp.status_code}"}

    try:
        delivery = resp.json().get("telegram_delivery")
    except Exception as exc:
        return {"ok": False, "message_id": None, "error": f"bad response: {str(exc)[:200]}"}
    if not isinstance(delivery, dict):
        return {"ok": False, "message_id": None, "error": "no telegram_delivery in response"}
    return delivery
```

- [ ] **Step 5: Rodar e confirmar verde**

Run: `.venv/bin/python -m pytest tests/test_channel_publisher.py -v`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add execution/integrations/channel_publisher.py tests/test_channel_publisher.py
git commit -m "feat: channel_publisher — scripts GH Actions publicam no canal via /store-draft"
```

(Se o Step 1 exigiu adicionar `requests` ao requirements da raiz, incluir `requirements.txt` no `git add`.)

---

### Task 2: Roteamento — 5 workflows viram canal

**Files:**
- Modify: `webhook/bot/routing.py:9` (frozenset `CLIENT_WORKFLOWS`)
- Modify: `tests/test_bot_routing.py` (workflows internos deixaram de existir entre os 5)
- Modify: `tests/test_store_draft_delivery_routing.py` (exemplo interno muda de `morning_check` pra `watchdog`)

**Interfaces:**
- Produces: `resolve_destination()` retorna `DEST_CLIENT_CHANNEL` para os 5 workflows (`daily_report`, `market_news`, `platts_reports`, `morning_check`, `baltic_ingestion`); `DEST_INTERNAL` só pra desconhecidos/None.

- [ ] **Step 1: Atualizar os testes primeiro (falham contra o código atual)**

Em `tests/test_bot_routing.py`, substituir os dois primeiros testes:

```python
def test_client_workflows_route_to_channel():
    from bot.routing import resolve_destination, DEST_CLIENT_CHANNEL
    for wf in (
        "daily_report", "market_news", "platts_reports",
        "morning_check", "baltic_ingestion",
    ):
        assert resolve_destination(wf) == DEST_CLIENT_CHANNEL
```

por este (o teste `test_internal_workflows_route_to_internal` é REMOVIDO — morning_check e baltic_ingestion são conteúdo de cliente, ver spec §1). Manter `test_unknown_and_none_route_to_internal` e os 3 testes de `client_delivery_mode` intactos.

Em `tests/test_store_draft_delivery_routing.py`, no teste `test_internal_workflow_keeps_dm_broadcast`, trocar as duas ocorrências de `"morning_check"` por `"watchdog"` (workflow real que não é de cliente):

```python
        resp = await store_draft(_FakeRequest(_payload("watchdog")))
    body = json.loads(resp.body)
    dm_mock.assert_awaited_once_with("watchdog", "conteúdo")
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `.venv/bin/python -m pytest tests/test_bot_routing.py -v`
Expected: `test_client_workflows_route_to_channel` FAIL (morning_check resolve pra internal hoje)

- [ ] **Step 3: Implementar**

Em `webhook/bot/routing.py`, trocar:

```python
CLIENT_WORKFLOWS = frozenset({"daily_report", "market_news", "platts_reports"})
```

por:

```python
CLIENT_WORKFLOWS = frozenset({
    "daily_report",
    "market_news",
    "platts_reports",
    "morning_check",
    "baltic_ingestion",
})
```

- [ ] **Step 4: Rodar e confirmar verde (+ regressão)**

Run: `.venv/bin/python -m pytest tests/test_bot_routing.py tests/test_store_draft_delivery_routing.py tests/test_bot_delivery.py -v`
Expected: todos passed

- [ ] **Step 5: Commit**

```bash
git add webhook/bot/routing.py tests/test_bot_routing.py tests/test_store_draft_delivery_routing.py
git commit -m "feat(bot): morning_check e baltic_ingestion roteiam pro canal (são conteúdo de cliente)"
```

---

### Task 3: `send_daily_report.py` — extrai `deliver_message` + gate

**Files:**
- Modify: `execution/scripts/send_daily_report.py` (linhas 138-180: seção "3. Fetch Contacts" até o `logger.info` do broadcast — vira função módulo `deliver_message`)
- Test: `tests/test_send_daily_report_delivery.py`

**Interfaces:**
- Consumes: `channel_publisher.delivery_mode` / `publish_to_channel` (Task 1).
- Produces: `deliver_message(message: str, dry_run: bool, progress, bus, logger) -> None` — levanta `RuntimeError` se a publicação no canal falhar (job fica vermelho).

- [ ] **Step 1: Escrever os testes que falham**

Criar `tests/test_send_daily_report_delivery.py`:

```python
"""daily_report delivery gate: telegram → channel publish, uazapi → legacy."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

import pytest


@pytest.fixture
def mocks():
    return MagicMock(), MagicMock(), MagicMock()  # progress, bus, logger


def test_telegram_mode_publishes_to_channel(monkeypatch, mocks):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "telegram")
    progress, bus, log = mocks
    import execution.scripts.send_daily_report as sdr
    publish = MagicMock(return_value={"ok": True, "message_id": 1, "error": None})
    with patch("execution.integrations.channel_publisher.publish_to_channel", publish), \
         patch.object(sdr, "ContactsRepo") as repo:
        sdr.deliver_message("relatório", False, progress, bus, log)
    publish.assert_called_once()
    assert publish.call_args.args[0] == "daily_report"
    assert publish.call_args.args[1] == "relatório"
    repo.assert_not_called()  # legacy path untouched
    progress.finish_empty.assert_called_once()


def test_telegram_mode_failure_raises(monkeypatch, mocks):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "telegram")
    progress, bus, log = mocks
    import execution.scripts.send_daily_report as sdr
    publish = MagicMock(return_value={"ok": False, "message_id": None, "error": "boom"})
    with patch("execution.integrations.channel_publisher.publish_to_channel", publish):
        with pytest.raises(RuntimeError, match="boom"):
            sdr.deliver_message("relatório", False, progress, bus, log)


def test_telegram_mode_dry_run_skips_publish(monkeypatch, mocks):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "telegram")
    progress, bus, log = mocks
    import execution.scripts.send_daily_report as sdr
    publish = MagicMock()
    with patch("execution.integrations.channel_publisher.publish_to_channel", publish):
        sdr.deliver_message("relatório", True, progress, bus, log)
    publish.assert_not_called()
    progress.finish_empty.assert_called_once_with("dry-run")


def test_uazapi_mode_uses_legacy_path(monkeypatch, mocks):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "uazapi")
    progress, bus, log = mocks
    import execution.scripts.send_daily_report as sdr
    publish = MagicMock()
    repo = MagicMock()
    repo.return_value.list_by_list_code.return_value = []  # no contacts → early return
    with patch("execution.integrations.channel_publisher.publish_to_channel", publish), \
         patch.object(sdr, "ContactsRepo", repo):
        sdr.deliver_message("relatório", False, progress, bus, log)
    publish.assert_not_called()
    repo.return_value.list_by_list_code.assert_called_once_with("minerals_report")
    progress.finish_empty.assert_called_once_with("nenhum contato ativo")
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `.venv/bin/python -m pytest tests/test_send_daily_report_delivery.py -v`
Expected: FAIL com `AttributeError: module ... has no attribute 'deliver_message'`

- [ ] **Step 3: Implementar**

Em `execution/scripts/send_daily_report.py`:

1. Adicionar função módulo logo após `format_price_message` (antes do `@with_event_bus`):

```python
def deliver_message(message, dry_run, progress, bus, logger):
    """Deliver the formatted report.

    Default (CLIENT_DELIVERY_CHANNEL=telegram): single publish to the private
    Telegram channel via the webhook. Raises RuntimeError on publish failure
    so the GH Actions job goes red. 'uazapi' keeps the legacy WhatsApp
    fan-out (rollback path).
    """
    from execution.integrations.channel_publisher import delivery_mode, publish_to_channel

    if delivery_mode() == "telegram":
        if dry_run:
            logger.info("[DRY RUN] Would publish to Telegram channel")
            progress.finish_empty("dry-run")
            return
        bus.emit("step", label="Publicando no canal Telegram")
        progress.update("Publicando no canal Telegram...")
        draft_id = (
            f"daily_report-{os.getenv('GITHUB_RUN_ID') or datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        )
        result = publish_to_channel("daily_report", message, draft_id)
        if not result.get("ok"):
            raise RuntimeError(f"channel publish failed: {result.get('error')}")
        logger.info("Daily report published to Telegram channel.")
        progress.finish_empty("publicado no canal Telegram")
        return

    # ── legacy uazapi fan-out (rollback path) — moved verbatim from main() ──
    from execution.integrations.uazapi_client import UazapiClient

    logger.info("Fetching contacts...")
    contacts_repo = ContactsRepo()
    contacts = contacts_repo.list_by_list_code("minerals_report")

    if not contacts:
        logger.warning("No contacts found to send to.")
        progress.finish_empty("nenhum contato ativo")
        return

    uazapi = UazapiClient()
    delivery_contacts = [build_delivery_contact(c) for c in contacts]

    if dry_run:
        logger.info(f"[DRY RUN] Would send to {len(delivery_contacts)} contacts")
        progress.finish_empty("dry-run")
        return

    bus.emit("step", label=f"Enviando WhatsApp para {len(delivery_contacts)} contatos")
    progress.update(f"Enviando pra {len(delivery_contacts)} contatos... (0/{len(delivery_contacts)})")

    reporter = DeliveryReporter(
        workflow="daily_report",
        send_fn=uazapi.send_message,
        notify_telegram=False,
        gh_run_id=os.getenv("GITHUB_RUN_ID"),
    )
    report = reporter.dispatch(
        delivery_contacts,
        message,
        on_progress=progress.on_dispatch_tick,
    )

    asyncio.run(progress.finish(report, message=message))

    logger.info(
        f"Daily report broadcast complete. Sent: {report.success_count}, "
        f"Failed: {report.failure_count}"
    )
```

2. Em `main()`, substituir TODO o trecho das linhas 138-180 (do comentário `# 3. Fetch Contacts` até o `logger.info(f"Daily report broadcast complete...")` inclusive, incluindo os returns intermediários com `lseg.close()` — o `finally` já fecha o lseg) por:

```python
        # 3. Deliver (canal Telegram por default; uazapi via CLIENT_DELIVERY_CHANNEL)
        deliver_message(message, args.dry_run, progress, bus, logger)
```

Nota: o `from execution.integrations.uazapi_client import UazapiClient` que estava dentro de `main()` foi movido pro branch legado de `deliver_message`. Nenhum outro import muda.

- [ ] **Step 4: Rodar e confirmar verde**

Run: `.venv/bin/python -m pytest tests/test_send_daily_report_delivery.py -v && .venv/bin/python -c "import execution.scripts.send_daily_report"`
Expected: 4 passed; import sem erro

- [ ] **Step 5: Commit**

```bash
git add execution/scripts/send_daily_report.py tests/test_send_daily_report_delivery.py
git commit -m "feat: daily_report publica no canal Telegram (gate CLIENT_DELIVERY_CHANNEL)"
```

---

### Task 4: `morning_check.py` — extrai `deliver_message` + gate

**Files:**
- Modify: `execution/scripts/morning_check.py` (seção de envio da PHASE 4: de `contacts_repo = ContactsRepo()` linha ~298 até o bloco PHASE 5 `set_sent_flag` linha ~338)
- Test: `tests/test_morning_check_delivery.py`

**Interfaces:**
- Consumes: `channel_publisher.delivery_mode` / `publish_to_channel` (Task 1).
- Produces: `deliver_message(message: str, dry_run: bool, progress, bus, logger) -> bool` — True quando entregou de fato (o caller usa pra setar o sent flag); levanta `RuntimeError` em falha de publicação.

- [ ] **Step 1: Escrever os testes que falham**

Criar `tests/test_morning_check_delivery.py`:

```python
"""morning_check delivery gate: telegram → channel publish, uazapi → legacy."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

import pytest


@pytest.fixture
def mocks():
    return MagicMock(), MagicMock(), MagicMock()  # progress, bus, logger


def test_telegram_mode_publishes_and_returns_true(monkeypatch, mocks):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "telegram")
    progress, bus, log = mocks
    import execution.scripts.morning_check as mc
    publish = MagicMock(return_value={"ok": True, "message_id": 2, "error": None})
    with patch("execution.integrations.channel_publisher.publish_to_channel", publish), \
         patch.object(mc, "ContactsRepo") as repo:
        sent = mc.deliver_message("preços platts", False, progress, bus, log)
    assert sent is True
    assert publish.call_args.args[0] == "morning_check"
    repo.assert_not_called()


def test_telegram_mode_failure_raises(monkeypatch, mocks):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "telegram")
    progress, bus, log = mocks
    import execution.scripts.morning_check as mc
    publish = MagicMock(return_value={"ok": False, "message_id": None, "error": "sem canal"})
    with patch("execution.integrations.channel_publisher.publish_to_channel", publish):
        with pytest.raises(RuntimeError, match="sem canal"):
            mc.deliver_message("m", False, progress, bus, log)


def test_telegram_mode_dry_run_returns_false(monkeypatch, mocks):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "telegram")
    progress, bus, log = mocks
    import execution.scripts.morning_check as mc
    publish = MagicMock()
    with patch("execution.integrations.channel_publisher.publish_to_channel", publish):
        sent = mc.deliver_message("m", True, progress, bus, log)
    assert sent is False
    publish.assert_not_called()


def test_uazapi_mode_no_contacts_returns_false(monkeypatch, mocks):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "uazapi")
    progress, bus, log = mocks
    import execution.scripts.morning_check as mc
    publish = MagicMock()
    repo = MagicMock()
    repo.return_value.list_by_list_code.return_value = []
    with patch("execution.integrations.channel_publisher.publish_to_channel", publish), \
         patch.object(mc, "ContactsRepo", repo):
        sent = mc.deliver_message("m", False, progress, bus, log)
    assert sent is False
    publish.assert_not_called()
    repo.return_value.list_by_list_code.assert_called_once_with("minerals_report")
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `.venv/bin/python -m pytest tests/test_morning_check_delivery.py -v`
Expected: FAIL com `AttributeError: ... has no attribute 'deliver_message'`

- [ ] **Step 3: Implementar**

Em `execution/scripts/morning_check.py`:

1. Adicionar função módulo antes do `@with_event_bus` (a parte legado é o código atual das linhas ~298-332 **movido verbatim**, só trocando `args.dry_run` por `dry_run` e adicionando os `return True/False`):

```python
def deliver_message(message, dry_run, progress, bus, logger) -> bool:
    """Deliver the morning report. Returns True when actually delivered
    (caller sets the daily sent flag). Raises RuntimeError when the channel
    publish fails so the GH Actions job goes red.
    """
    from execution.integrations.channel_publisher import delivery_mode, publish_to_channel

    if delivery_mode() == "telegram":
        if dry_run:
            logger.info("[DRY RUN] Would publish to Telegram channel")
            progress.finish_empty("dry-run")
            return False
        bus.emit("step", label="Publicando no canal Telegram")
        progress.update("Publicando no canal Telegram...")
        draft_id = (
            f"morning_check-{os.getenv('GITHUB_RUN_ID') or datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        )
        result = publish_to_channel("morning_check", message, draft_id)
        if not result.get("ok"):
            raise RuntimeError(f"channel publish failed: {result.get('error')}")
        logger.info("Morning report published to Telegram channel.")
        progress.finish_empty("publicado no canal Telegram")
        return True

    # ── legacy uazapi fan-out (rollback path) — moved verbatim from main() ──
    contacts_repo = ContactsRepo()
    contacts = contacts_repo.list_by_list_code("minerals_report")

    if not contacts:
        logger.warning("No contacts found.")
        progress.finish_empty("nenhum contato ativo")
        return False

    uazapi = UazapiClient()

    delivery_contacts = [build_delivery_contact(c) for c in contacts]

    if dry_run:
        logger.info(f"[DRY RUN] Would send to {len(delivery_contacts)} contacts")
        progress.finish_empty("dry-run")
        return False

    bus.emit("step", label=f"Enviando WhatsApp para {len(delivery_contacts)} contatos")
    progress.update(f"Enviando pra {len(delivery_contacts)} contatos... (0/{len(delivery_contacts)})")

    reporter = DeliveryReporter(
        workflow="morning_check",
        send_fn=uazapi.send_message,
        notify_telegram=False,
        gh_run_id=os.getenv("GITHUB_RUN_ID"),
    )
    report = reporter.dispatch(
        delivery_contacts,
        message,
        on_progress=progress.on_dispatch_tick,
    )

    asyncio.run(progress.finish(report, message=message))

    logger.info(
        f"Broadcast complete. Sent: {report.success_count}, Failed: {report.failure_count}"
    )
    return True
```

2. Em `main()`, substituir o trecho de `contacts_repo = ContactsRepo()` (após o preview de dry-run) até o final do bloco PHASE 5 (`state_store.set_sent_flag(...)`) por:

```python
        sent = deliver_message(message, args.dry_run, progress, bus, logger)

        # ── PHASE 5: commit success — set sent flag ───────────────────────────
        if sent and not args.dry_run:
            state_store.set_sent_flag(sent_key, ttl_seconds=_SENT_FLAG_TTL_SEC)
```

(Comportamento preservado: no fluxo original o flag não era setado quando o envio abortava por falta de contatos — o `return False` mantém isso.)

- [ ] **Step 4: Rodar e confirmar verde**

Run: `.venv/bin/python -m pytest tests/test_morning_check_delivery.py tests/test_morning_check_idempotency.py -v && .venv/bin/python -c "import execution.scripts.morning_check"`
Expected: todos passed; import sem erro

- [ ] **Step 5: Commit**

```bash
git add execution/scripts/morning_check.py tests/test_morning_check_delivery.py
git commit -m "feat: morning_check publica no canal Telegram (gate CLIENT_DELIVERY_CHANNEL)"
```

---

### Task 5: `baltic_ingestion.py` — extrai `deliver_message` async + gate

**Files:**
- Modify: `execution/scripts/baltic_ingestion.py` (seção de envio: de `message = format_whatsapp_message(data)` linha ~361 até `await reporter.finish(report=report, message=message)` linha ~404)
- Test: `tests/test_baltic_delivery.py`

**Interfaces:**
- Consumes: `channel_publisher.delivery_mode` / `publish_to_channel` (Task 1); `WORKFLOW_NAME` (constante já existente no script).
- Produces: `async deliver_message(message: str, dry_run: bool, reporter, bus, logger) -> bool` — True quando entregou; levanta `RuntimeError` em falha de publicação. **Faz o `await reporter.finish(...)` internamente nos dois branches** (espelha o fluxo atual).

- [ ] **Step 1: Escrever os testes que falham**

Criar `tests/test_baltic_delivery.py`:

```python
"""baltic_ingestion delivery gate: telegram → channel publish, uazapi → legacy."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

import pytest


@pytest.fixture
def mocks():
    reporter = MagicMock()
    reporter.step = AsyncMock()
    reporter.finish = AsyncMock()
    return reporter, MagicMock(), MagicMock()  # reporter, bus, logger


@pytest.mark.asyncio
async def test_telegram_mode_publishes_and_finishes(monkeypatch, mocks):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "telegram")
    reporter, bus, log = mocks
    import execution.scripts.baltic_ingestion as bi
    publish = MagicMock(return_value={"ok": True, "message_id": 3, "error": None})
    with patch("execution.integrations.channel_publisher.publish_to_channel", publish), \
         patch.object(bi, "ContactsRepo") as repo:
        sent = await bi.deliver_message("bdi msg", False, reporter, bus, log)
    assert sent is True
    assert publish.call_args.args[0] == bi.WORKFLOW_NAME
    repo.assert_not_called()
    reporter.finish.assert_awaited_once()


@pytest.mark.asyncio
async def test_telegram_mode_failure_raises(monkeypatch, mocks):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "telegram")
    reporter, bus, log = mocks
    import execution.scripts.baltic_ingestion as bi
    publish = MagicMock(return_value={"ok": False, "message_id": None, "error": "erro X"})
    with patch("execution.integrations.channel_publisher.publish_to_channel", publish):
        with pytest.raises(RuntimeError, match="erro X"):
            await bi.deliver_message("m", False, reporter, bus, log)
    reporter.finish.assert_not_awaited()


@pytest.mark.asyncio
async def test_telegram_mode_dry_run_returns_false(monkeypatch, mocks):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "telegram")
    reporter, bus, log = mocks
    import execution.scripts.baltic_ingestion as bi
    publish = MagicMock()
    with patch("execution.integrations.channel_publisher.publish_to_channel", publish):
        sent = await bi.deliver_message("m", True, reporter, bus, log)
    assert sent is False
    publish.assert_not_called()


@pytest.mark.asyncio
async def test_uazapi_mode_no_contacts_returns_false(monkeypatch, mocks):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "uazapi")
    reporter, bus, log = mocks
    import execution.scripts.baltic_ingestion as bi
    publish = MagicMock()
    repo = MagicMock()
    repo.return_value.list_by_list_code.return_value = []
    with patch("execution.integrations.channel_publisher.publish_to_channel", publish), \
         patch.object(bi, "ContactsRepo", repo):
        sent = await bi.deliver_message("m", False, reporter, bus, log)
    assert sent is False
    publish.assert_not_called()
    reporter.finish.assert_awaited_once()
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `.venv/bin/python -m pytest tests/test_baltic_delivery.py -v`
Expected: FAIL com `AttributeError: ... has no attribute 'deliver_message'`

- [ ] **Step 3: Implementar**

Em `execution/scripts/baltic_ingestion.py`:

1. Adicionar função módulo (async) antes da main async do script. O branch legado é o código atual (do `contacts_repo = ContactsRepo()` até o `await reporter.finish(report=report, message=message)`) **movido verbatim**, com `args.dry_run` → `dry_run` e os returns booleanos:

```python
async def deliver_message(message, dry_run, reporter, bus, logger) -> bool:
    """Deliver the Baltic report. Returns True when actually delivered
    (caller sets the daily sent flag). Raises RuntimeError when the channel
    publish fails. Calls reporter.finish() in both branches (mirrors the
    original flow).
    """
    from execution.integrations.channel_publisher import delivery_mode, publish_to_channel

    if delivery_mode() == "telegram":
        if dry_run:
            logger.info("[DRY RUN] Would publish to Telegram channel")
            return False
        bus.emit("step", label="Publicando no canal Telegram")
        draft_id = (
            f"baltic_ingestion-{os.getenv('GITHUB_RUN_ID') or datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        )
        result = await asyncio.to_thread(publish_to_channel, WORKFLOW_NAME, message, draft_id)
        if not result.get("ok"):
            raise RuntimeError(f"channel publish failed: {result.get('error')}")
        logger.info("Baltic report published to Telegram channel.")
        await reporter.step("Canal Telegram", "publicado (1 post)")
        await reporter.finish(message=message)
        return True

    # ── legacy uazapi fan-out (rollback path) — moved verbatim from main ──
    bus.emit("step", label="Enviando WhatsApp")
    contacts_repo = ContactsRepo()
    contacts = await asyncio.to_thread(
        contacts_repo.list_by_list_code, "minerals_report"
    )
    uazapi = UazapiClient()

    delivery_contacts = [build_delivery_contact(c) for c in contacts]

    if not delivery_contacts:
        logger.warning("No active contacts found.")
        await reporter.step("No contacts", "no active contacts found", level="info")
        await reporter.finish()
        return False

    if dry_run:
        logger.info(f"[DRY RUN] Would send to {len(delivery_contacts)} contacts")
        await reporter.finish()
        return False

    delivery_reporter = DeliveryReporter(
        workflow=WORKFLOW_NAME,
        send_fn=uazapi.send_message,
        notify_telegram=False,
        gh_run_id=os.getenv("GITHUB_RUN_ID"),
    )
    report = await asyncio.to_thread(
        delivery_reporter.dispatch,
        delivery_contacts,
        message,
    )

    await reporter.step(
        "Postgres upsert",
        f"{report.success_count} sent, {report.failure_count} failed of {report.total} contacts",
    )
    logger.info(
        f"Baltic broadcast complete. Sent: {report.success_count}, "
        f"Failed: {report.failure_count}"
    )
    await reporter.finish(report=report, message=message)
    return True
```

**Atenção do implementador:** conferir no arquivo real se o fluxo original tem guard de dry-run nessa seção e se `reporter.finish` é chamado com outros argumentos — o branch legado deve reproduzir o comportamento atual exatamente (incluindo a ordem flag→finish, ver item 2). Se o original NÃO tem guard de dry-run ali (tratado antes), manter o guard novo mesmo assim — é inofensivo e protege o branch.

2. Em `main` (a função decorada do script), substituir o trecho de `message = format_whatsapp_message(data)` em diante (o bloco de envio + PHASE 6c + finish) por:

```python
        message = format_whatsapp_message(data)

        sent = await deliver_message(message, args.dry_run, reporter, bus, logger)

        # ── PHASE 6c: commit success — set sent flag ─────────────────────────
        if sent and not args.dry_run:
            await asyncio.to_thread(state_store.set_sent_flag, sent_key, _SENT_FLAG_TTL_SEC)
```

- [ ] **Step 4: Rodar e confirmar verde**

Run: `.venv/bin/python -m pytest tests/test_baltic_delivery.py tests/test_baltic_ingestion_idempotency.py -v && .venv/bin/python -c "import execution.scripts.baltic_ingestion"`
Expected: todos passed; import sem erro

- [ ] **Step 5: Commit**

```bash
git add execution/scripts/baltic_ingestion.py tests/test_baltic_delivery.py
git commit -m "feat: baltic_ingestion publica no canal Telegram (gate CLIENT_DELIVERY_CHANNEL)"
```

---

### Task 6: GH Actions — env `WEBHOOK_BASE_URL` + `CLIENT_DELIVERY_CHANNEL`

**Files:**
- Modify: `.github/workflows/daily_report.yml` (bloco `env:` do step "Run Report")
- Modify: `.github/workflows/morning_check.yml` (bloco `env:` do step de execução)
- Modify: `.github/workflows/baltic_ingestion.yml` (bloco `env:` do step de execução)

**Interfaces:**
- Consumes: `channel_publisher` lê `WEBHOOK_BASE_URL` e `CLIENT_DELIVERY_CHANNEL` do ambiente (Task 1).

- [ ] **Step 1: Adicionar as 2 linhas ao bloco `env:` de cada um dos 3 workflows**

Em cada arquivo, dentro do `env:` do step que roda o script Python, adicionar:

```yaml
          WEBHOOK_BASE_URL: ${{ vars.WEBHOOK_BASE_URL }}
          CLIENT_DELIVERY_CHANNEL: ${{ vars.CLIENT_DELIVERY_CHANNEL }}
```

(`vars`, não `secrets` — a URL não é sensível e o rollback precisa ser editável na UI. Var ausente → string vazia → `delivery_mode()` default telegram; `WEBHOOK_BASE_URL` vazio → job falha com erro claro, que é o desejado.)

- [ ] **Step 2: Validar sintaxe YAML**

Run: `.venv/bin/python -c "import yaml; [yaml.safe_load(open(f)) for f in ['.github/workflows/daily_report.yml', '.github/workflows/morning_check.yml', '.github/workflows/baltic_ingestion.yml']]; print('yaml ok')"`
Expected: `yaml ok`

- [ ] **Step 3: Criar as variables no repositório**

```bash
gh variable set WEBHOOK_BASE_URL --body "https://web-production-0d909.up.railway.app"
gh variable set CLIENT_DELIVERY_CHANNEL --body "telegram"
gh variable list
```

Expected: as duas variables listadas.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/daily_report.yml .github/workflows/morning_check.yml .github/workflows/baltic_ingestion.yml
git commit -m "ci: WEBHOOK_BASE_URL e CLIENT_DELIVERY_CHANNEL nos crons de broadcast"
```

---

### Task 7: Fase 2 — `split_for_expandable` (citação expansível)

**Files:**
- Modify: `webhook/bot/channel_delivery.py` (nova função após `to_telegram_html`, linha ~53; integração no bloco de setup de `post_report_to_channel`, linhas 91-96)
- Test: `tests/test_channel_delivery.py` (novos testes ao final)

**Interfaces:**
- Consumes: `to_telegram_html` (existente).
- Produces: `split_for_expandable(text: str, threshold: int = 900) -> tuple[str, str | None]`; `EXPANDABLE_THRESHOLD = 900`. Comportamento de `post_report_to_channel` inalterado pra posts ≤900 chars.

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao final de `tests/test_channel_delivery.py`:

```python
# ── split_for_expandable (estética v2) ──


def _long_curator_message() -> str:
    """Realistic Curator-shaped message, > 900 chars, 5 blocks."""
    header = "📊 *MINERALS TRADING*\n*IODEX $107,90 — Foco em Fósforo*\n`IRON ORE · 09/JUL`\n─────────────────"
    lead = "IODEX em `$107,90/dmt` CFR North China (-5¢ d/d). Retorno da Jimblebar elevou share de P alto no medium-grade."
    trades = "*TRADES (CFR Qingdao)*\n- BHP · NHGF 61,2% Fe — `90k mt` a IODEX mai `-$1,83/dmt`\n- Rio Tinto · PBF 61% Fe — `170k mt` a IODEX jun `+$1,45/dmt`"
    port = "*PORT-STOCK (FOT)*\n- IOPEX North — `¥790/wmt` (-¥5)\n- IOPEX East — `¥782/wmt` (-¥2)"
    watch = "Watch: feriado May Day (1-5/mai) pode puxar restock. " + "Contexto adicional de mercado. " * 20
    return "\n\n".join([header, lead, trades, port, watch])


def test_split_short_message_intact():
    from bot.channel_delivery import split_for_expandable
    text = "📊 *MINERALS TRADING*\n\nLead curto.\n\n*SEÇÃO*\n- item"
    assert split_for_expandable(text) == (text, None)


def test_split_exactly_at_threshold_intact():
    from bot.channel_delivery import split_for_expandable
    text = "a\n\nb\n\n" + "c" * 894  # len == 900
    assert len(text) == 900
    assert split_for_expandable(text) == (text, None)


def test_split_long_message_keeps_header_and_lead_visible():
    from bot.channel_delivery import split_for_expandable
    msg = _long_curator_message()
    assert len(msg) > 900
    visible, collapsed = split_for_expandable(msg)
    assert visible.startswith("📊 *MINERALS TRADING*")
    assert "IODEX em `$107,90/dmt`" in visible          # lead (bloco 1) visível
    assert "*TRADES (CFR Qingdao)*" not in visible       # seções colapsadas
    assert collapsed is not None
    assert collapsed.startswith("*TRADES (CFR Qingdao)*")
    assert "Watch:" in collapsed


def test_split_long_but_unstructured_intact():
    from bot.channel_delivery import split_for_expandable
    text = "linha única sem blocos " * 60  # > 900 chars, sem \n\n
    assert split_for_expandable(text) == (text, None)


def test_split_two_blocks_intact():
    from bot.channel_delivery import split_for_expandable
    text = ("bloco um " * 60) + "\n\n" + ("bloco dois " * 40)  # > 900, só 2 blocos
    assert split_for_expandable(text) == (text, None)


@pytest.mark.asyncio
async def test_post_long_message_wraps_sections_in_expandable(channel, mock_bot):
    result = await channel.post_report_to_channel(_long_curator_message())
    assert result["ok"] is True
    sent_text = mock_bot.send_message.await_args.args[1]
    assert "<blockquote expandable>" in sent_text
    assert sent_text.rstrip().endswith("</blockquote>")
    # header/lead fora do blockquote, convertidos
    before_quote = sent_text.split("<blockquote expandable>")[0]
    assert "<b>MINERALS TRADING</b>" in before_quote
    # seções dentro do blockquote, com conversão aplicada
    inside = sent_text.split("<blockquote expandable>")[1]
    assert "<b>TRADES (CFR Qingdao)</b>" in inside


@pytest.mark.asyncio
async def test_post_short_message_has_no_blockquote(channel, mock_bot):
    result = await channel.post_report_to_channel("*Post* curto do dia")
    assert result["ok"] is True
    sent_text = mock_bot.send_message.await_args.args[1]
    assert "<blockquote" not in sent_text
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `.venv/bin/python -m pytest tests/test_channel_delivery.py -v`
Expected: novos testes FAIL (`ImportError`/`AttributeError: split_for_expandable`); os 13 existentes passam.

- [ ] **Step 3: Implementar**

Em `webhook/bot/channel_delivery.py`:

1. Após a função `to_telegram_html` (linha ~53), adicionar:

```python
EXPANDABLE_THRESHOLD = 900


def split_for_expandable(
    text: str, threshold: int = EXPANDABLE_THRESHOLD,
) -> tuple[str, str | None]:
    """Split a long raw message into (visible, collapsed) at block boundaries.

    Blocks are paragraphs separated by blank lines. Visible = first two
    blocks (Curator header + lead, which carries the headline number).
    Short (<= threshold) or unstructured (< 3 blocks) messages come back
    unchanged as (text, None) — a malformed post never breaks.
    """
    if len(text) <= threshold:
        return (text, None)
    blocks = [b for b in text.split("\n\n") if b.strip()]
    if len(blocks) < 3:
        return (text, None)
    visible = "\n\n".join(blocks[:2])
    collapsed = "\n\n".join(blocks[2:])
    return (visible, collapsed)
```

2. No bloco de setup de `post_report_to_channel` (linhas 91-96), trocar:

```python
    try:
        bot = get_bot()
        text = to_telegram_html(message[:RAW_TEXT_LIMIT])
    except Exception as exc:
        logger.error(f"post_report_to_channel setup failed: {exc}")
        return {"ok": False, "message_id": None, "error": str(exc)[:300]}
```

por:

```python
    try:
        bot = get_bot()
        raw = message[:RAW_TEXT_LIMIT]
        try:
            visible_raw, collapsed_raw = split_for_expandable(raw)
        except Exception as split_exc:
            # Split must never block delivery — degrade to the whole post.
            logger.warning(f"split_for_expandable failed, posting whole: {split_exc}")
            visible_raw, collapsed_raw = raw, None
        text = to_telegram_html(visible_raw)
        if collapsed_raw is not None:
            text = (
                f"{text}\n\n<blockquote expandable>"
                f"{to_telegram_html(collapsed_raw)}</blockquote>"
            )
    except Exception as exc:
        logger.error(f"post_report_to_channel setup failed: {exc}")
        return {"ok": False, "message_id": None, "error": str(exc)[:300]}
```

- [ ] **Step 4: Rodar e confirmar verde**

Run: `.venv/bin/python -m pytest tests/test_channel_delivery.py -v`
Expected: 20 passed (13 existentes + 7 novos)

- [ ] **Step 5: Commit**

```bash
git add webhook/bot/channel_delivery.py tests/test_channel_delivery.py
git commit -m "feat(bot): citação expansível pra posts longos no canal (split_for_expandable)"
```

---

### Task 8: Verificação final + status do spec

**Files:**
- Modify: `docs/superpowers/specs/2026-07-09-crons-canal-estetica-v2-design.md` (linha de status)

- [ ] **Step 1: Suite completa**

Run: `.venv/bin/python -m pytest`
Expected: tudo verde (baseline 868 − 1 teste removido no roteamento + 26 novos ≈ 893). Falha em teste pré-existente = regressão desta feature — investigar antes de seguir.

- [ ] **Step 2: Atualizar status do spec**

Trocar a linha:

```markdown
- **Status:** Aprovado (design); aguardando plano de implementação
```

por:

```markdown
- **Status:** Implementado (plano: docs/superpowers/plans/2026-07-09-crons-canal-estetica-v2.md); pendente validação do 1º cron real
```

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-07-09-crons-canal-estetica-v2-design.md docs/superpowers/plans/2026-07-09-crons-canal-estetica-v2.md
git commit -m "docs: spec+plano crons→canal e estética v2"
```

- [ ] **Step 4: Validação pós-merge (operador/controller — fora dos subagentes)**

1. Smoke real do expansível: postar 1 mensagem longa estilo Curator no canal oficial e validar visualmente (controller faz da sessão, como em 2026-07-09).
2. Disparar `daily_report` manualmente (`gh workflow run daily_report.yml`) e confirmar o post no canal.
3. Observar o primeiro disparo agendado de `morning_check` e `baltic_ingestion` no canal.
4. Rollback disponível: `gh variable set CLIENT_DELIVERY_CHANNEL --body "uazapi"`.
