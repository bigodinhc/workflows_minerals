# Telegram Channel Delivery — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrar a distribuição de relatórios de cliente do WhatsApp (uazapi) para um canal privado do Telegram, com convite/aprovação de entrada e rollback por flag.

**Architecture:** Um canal privado recebe os workflows de cliente (`daily_report`, `market_news`, `platts_reports`) via um novo módulo `channel_delivery` (post único: resumo HTML + PDF anexo). O roteamento acontece em 3 pontos existentes — `/store-draft` (entrega direta), `process_approval_async` (aprovação de texto) e `dispatch_document` (PDF OneDrive) — gateados pelo mapa workflow→destino e pela flag `CLIENT_DELIVERY_CHANNEL`. Workflows internos (`morning_check`, `baltic_ingestion`) mantêm o DM broadcast atual. Onboarding dos ~74 contatos por invite link com join request aprovado pelo admin.

**Tech Stack:** Python 3, aiogram 3.x (Bot API oficial), aiohttp, Redis (fakeredis nos testes), pytest + pytest-asyncio, qrcode[pil].

**Spec:** `docs/superpowers/specs/2026-07-09-telegram-channel-delivery-design.md`

## Global Constraints

- Branch de trabalho: `feat/telegram-channel-delivery` (já ativa).
- Env vars novas: `TELEGRAM_CLIENT_CHANNEL_ID` (id do canal, ex. `-1001234567890`), `CLIENT_DELIVERY_CHANNEL` (`telegram` | `uazapi`, **default `telegram`**).
- Workflows de cliente: `daily_report`, `market_news`, `platts_reports`. Internos: `morning_check`, `baltic_ingestion`.
- Funções de entrega **nunca levantam exceção** — retornam dict de status e logam (padrão dos sinks de `execution/core/event_bus.py`).
- Formatação do canal: `parse_mode="HTML"`; o corpo passa por `to_telegram_html()` — escapa `& < >` e converte a sintaxe WhatsApp do Curator (`*negrito*`, `_itálico_`, `` `mono` ``) em tags HTML. Marcadores desbalanceados ficam literais (nunca quebram o parse). Os prompts Writer/Critique/Curator/Adjuster **não mudam** — a conversão acontece só na fronteira do canal, então o rollback uazapi recebe o texto original intacto.
- **Bot API oficial apenas** — nunca userbot/MTProto.
- Restrição da API do Telegram: `member_limit` e `creates_join_request` são **mutuamente exclusivos** em `create_chat_invite_link`. Escolhemos `creates_join_request=True` (controle por aprovação); `member_limit` fica de fora.
- Testes rodam com: `.venv/bin/python -m pytest tests/<arquivo> -v` (venv da raiz; pytest.ini já aponta `testpaths = tests`). Async tests usam decorator `@pytest.mark.asyncio` (não há asyncio_mode global).
- Dependências: `qrcode[pil]` entra **só** em `webhook/requirements.txt` (o bot roda no Railway a partir dele; o `requirements.txt` da raiz é para GH Actions, que não roda o bot). Instalar no venv local com `uv pip install` (pip do sistema está quebrado nesta máquina).
- Estilo: imutabilidade (criar dicts novos com `{**x, ...}`), arquivos pequenos, commits convencionais em PT sem attribution.
- Código uazapi **permanece** no repo (rollback via `CLIENT_DELIVERY_CHANNEL=uazapi`); nada de deletar caminho WhatsApp.

---

### Task 1: Módulo de roteamento + env vars

**Files:**
- Create: `webhook/bot/routing.py`
- Modify: `webhook/bot/config.py` (adicionar 1 constante após linha 26, `TELEGRAM_WEBHOOK_URL`)
- Modify: `.env.example` (documentar as 2 vars novas, ao final do arquivo)
- Test: `tests/test_bot_routing.py`

**Interfaces:**
- Consumes: nada (módulo folha; lê `os.environ` em tempo de chamada).
- Produces:
  - `routing.CLIENT_WORKFLOWS: frozenset[str]`
  - `routing.DEST_CLIENT_CHANNEL = "client_channel"`, `routing.DEST_INTERNAL = "internal"`
  - `routing.resolve_destination(workflow_type: str | None) -> str`
  - `routing.client_delivery_mode() -> str` (`"telegram"` ou `"uazapi"`)
  - `config.TELEGRAM_CLIENT_CHANNEL_ID: str` (Tasks 2, 6, 7 dependem)

- [ ] **Step 1: Escrever os testes que falham**

Criar `tests/test_bot_routing.py`:

```python
"""Tests for workflow → delivery destination routing."""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

import pytest


def test_client_workflows_route_to_channel():
    from bot.routing import resolve_destination, DEST_CLIENT_CHANNEL
    for wf in ("daily_report", "market_news", "platts_reports"):
        assert resolve_destination(wf) == DEST_CLIENT_CHANNEL


def test_internal_workflows_route_to_internal():
    from bot.routing import resolve_destination, DEST_INTERNAL
    for wf in ("morning_check", "baltic_ingestion"):
        assert resolve_destination(wf) == DEST_INTERNAL


def test_unknown_and_none_route_to_internal():
    from bot.routing import resolve_destination, DEST_INTERNAL
    assert resolve_destination("something_new") == DEST_INTERNAL
    assert resolve_destination(None) == DEST_INTERNAL


def test_delivery_mode_defaults_to_telegram(monkeypatch):
    monkeypatch.delenv("CLIENT_DELIVERY_CHANNEL", raising=False)
    from bot.routing import client_delivery_mode
    assert client_delivery_mode() == "telegram"


def test_delivery_mode_uazapi(monkeypatch):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "uazapi")
    from bot.routing import client_delivery_mode
    assert client_delivery_mode() == "uazapi"


def test_delivery_mode_garbage_falls_back_to_telegram(monkeypatch):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "smoke-signals")
    from bot.routing import client_delivery_mode
    assert client_delivery_mode() == "telegram"
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `.venv/bin/python -m pytest tests/test_bot_routing.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'bot.routing'`

- [ ] **Step 3: Implementar**

Criar `webhook/bot/routing.py`:

```python
"""Workflow → delivery destination routing.

Client-facing workflows are broadcast as a single post to the private
Telegram channel; internal/operational workflows keep the current
DM-to-subscribers path. CLIENT_DELIVERY_CHANNEL flips client content
back to the legacy uazapi path for rollback.
"""

from __future__ import annotations

import os

CLIENT_WORKFLOWS = frozenset({"daily_report", "market_news", "platts_reports"})

DEST_CLIENT_CHANNEL = "client_channel"
DEST_INTERNAL = "internal"


def resolve_destination(workflow_type: str | None) -> str:
    """Return DEST_CLIENT_CHANNEL for client workflows, DEST_INTERNAL otherwise."""
    if workflow_type in CLIENT_WORKFLOWS:
        return DEST_CLIENT_CHANNEL
    return DEST_INTERNAL


def client_delivery_mode() -> str:
    """'telegram' (default) or 'uazapi' (legacy rollback).

    Read at call time (not import) so tests and redeploys pick up env changes.
    """
    mode = os.getenv("CLIENT_DELIVERY_CHANNEL", "telegram").strip().lower()
    return "uazapi" if mode == "uazapi" else "telegram"
```

Em `webhook/bot/config.py`, logo após a linha `TELEGRAM_WEBHOOK_URL = ...` (linha 26), adicionar:

```python
TELEGRAM_CLIENT_CHANNEL_ID = os.getenv("TELEGRAM_CLIENT_CHANNEL_ID", "").strip()
```

Ao final de `.env.example`, adicionar:

```bash
# ── Canal privado de clientes (Telegram) ──
# Id do canal privado onde relatórios de cliente são postados (bot precisa ser admin).
# Formato: -100XXXXXXXXXX
TELEGRAM_CLIENT_CHANNEL_ID=

# Destino do conteúdo de cliente: "telegram" (canal privado, default) ou
# "uazapi" (rollback para o broadcast WhatsApp legado).
CLIENT_DELIVERY_CHANNEL=telegram
```

- [ ] **Step 4: Rodar e confirmar verde**

Run: `.venv/bin/python -m pytest tests/test_bot_routing.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add webhook/bot/routing.py webhook/bot/config.py .env.example tests/test_bot_routing.py
git commit -m "feat(bot): mapa workflow→destino e flag CLIENT_DELIVERY_CHANNEL"
```

---

### Task 2: Módulo `channel_delivery` (post no canal)

**Files:**
- Create: `webhook/bot/channel_delivery.py`
- Test: `tests/test_channel_delivery.py`

**Interfaces:**
- Consumes: `bot.config.get_bot()` (Bot aiogram singleton), `bot.config.TELEGRAM_CLIENT_CHANNEL_ID` (Task 1).
- Produces:
  - `escape_html(text: str) -> str`
  - `to_telegram_html(text: str) -> str` — escapa `& < >` e converte sintaxe WhatsApp (```` ```bloco``` ```` → `<pre>`, `` `x` `` → `<code>`, `*x*` → `<b>`, `_x_` → `<i>`); marcador desbalanceado fica literal
  - `post_report_to_channel(message: str, pdf: bytes | None = None, pdf_filename: str = "report.pdf", *, silent: bool = False, pin: bool = False) -> dict` — retorna `{"ok": bool, "message_id": int | None, "error": str | None}`; **nunca levanta**. Tasks 3, 4 e 5 chamam esta função.

- [ ] **Step 1: Escrever os testes que falham**

Criar `tests/test_channel_delivery.py`:

```python
"""Tests for posting client reports to the private Telegram channel."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

import pytest
from aiogram.exceptions import TelegramRetryAfter


def _retry_after(seconds: int = 0) -> TelegramRetryAfter:
    return TelegramRetryAfter(method=MagicMock(), message="flood", retry_after=seconds)


@pytest.fixture
def mock_bot():
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    bot.send_document = AsyncMock(return_value=MagicMock(message_id=43))
    bot.pin_chat_message = AsyncMock()
    return bot


@pytest.fixture
def channel(mock_bot):
    """Patch bot + channel id; yields the module under test."""
    import bot.channel_delivery as cd
    with patch.object(cd, "get_bot", return_value=mock_bot), \
         patch.object(cd, "TELEGRAM_CLIENT_CHANNEL_ID", "-1001234"):
        yield cd


def test_escape_html():
    from bot.channel_delivery import escape_html
    assert escape_html("a < b & c > d") == "a &lt; b &amp; c &gt; d"
    assert escape_html('say "hi"') == 'say "hi"'  # quotes stay readable


def test_to_telegram_html_converts_whatsapp_markers():
    from bot.channel_delivery import to_telegram_html
    assert to_telegram_html("*IRON ORE DAILY*") == "<b>IRON ORE DAILY</b>"
    assert to_telegram_html("_em alta_") == "<i>em alta</i>"
    assert to_telegram_html("`62% Fe`") == "<code>62% Fe</code>"
    assert to_telegram_html("```col1  col2\n1     2```") == "<pre>col1  col2\n1     2</pre>"


def test_to_telegram_html_escapes_before_converting():
    from bot.channel_delivery import to_telegram_html
    assert to_telegram_html("*a < b & c*") == "<b>a &lt; b &amp; c</b>"


def test_to_telegram_html_unbalanced_markers_stay_literal():
    from bot.channel_delivery import to_telegram_html
    # a stray asterisk must never break the post — it renders as-is
    assert to_telegram_html("preço * volume") == "preço * volume"
    assert to_telegram_html("nota_de_rodapé") == "nota_de_rodapé"  # intra-word _ untouched
    assert to_telegram_html("*sem fechamento") == "*sem fechamento"


def test_to_telegram_html_mixed_message():
    from bot.channel_delivery import to_telegram_html
    src = "📊 *MINERALS TRADING*\n*Iron Ore Daily*\n`ATIVO · 09/JUL`\nPlatts: _firme_"
    out = to_telegram_html(src)
    assert "<b>MINERALS TRADING</b>" in out
    assert "<b>Iron Ore Daily</b>" in out
    assert "<code>ATIVO · 09/JUL</code>" in out
    assert "<i>firme</i>" in out


@pytest.mark.asyncio
async def test_post_text_only(channel, mock_bot):
    result = await channel.post_report_to_channel("*Preços* <em alta> & subindo")
    assert result == {"ok": True, "message_id": 42, "error": None}
    args, kwargs = mock_bot.send_message.await_args
    assert args[0] == "-1001234"
    assert "<b>Preços</b> &lt;em alta&gt; &amp;" in args[1]
    assert kwargs["parse_mode"] == "HTML"
    assert kwargs["disable_notification"] is False
    mock_bot.send_document.assert_not_awaited()
    mock_bot.pin_chat_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_with_pdf(channel, mock_bot):
    result = await channel.post_report_to_channel(
        "Relatório", pdf=b"%PDF-1.4 x", pdf_filename="Minerals_Report.pdf",
    )
    assert result["ok"] is True
    mock_bot.send_document.assert_awaited_once()
    _, kwargs = mock_bot.send_document.await_args
    assert kwargs["caption"] == "Minerals_Report.pdf"


@pytest.mark.asyncio
async def test_pdf_failure_does_not_block_text(channel, mock_bot):
    mock_bot.send_document.side_effect = RuntimeError("boom")
    result = await channel.post_report_to_channel("Relatório", pdf=b"%PDF")
    assert result["ok"] is True
    assert result["message_id"] == 42
    assert "pdf_send_failed" in result["error"]


@pytest.mark.asyncio
async def test_silent_and_pin(channel, mock_bot):
    result = await channel.post_report_to_channel("Msg", silent=True, pin=True)
    assert result["ok"] is True
    _, kwargs = mock_bot.send_message.await_args
    assert kwargs["disable_notification"] is True
    mock_bot.pin_chat_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_flood_wait_retries_then_succeeds(channel, mock_bot):
    mock_bot.send_message.side_effect = [
        _retry_after(0), MagicMock(message_id=99),
    ]
    result = await channel.post_report_to_channel("Msg")
    assert result["ok"] is True
    assert result["message_id"] == 99
    assert mock_bot.send_message.await_count == 2


@pytest.mark.asyncio
async def test_hard_failure_returns_error_dict(channel, mock_bot):
    mock_bot.send_message.side_effect = RuntimeError("bot is not a member")
    result = await channel.post_report_to_channel("Msg")
    assert result["ok"] is False
    assert result["message_id"] is None
    assert "bot is not a member" in result["error"]


@pytest.mark.asyncio
async def test_missing_channel_id_fails_cleanly(mock_bot):
    import bot.channel_delivery as cd
    with patch.object(cd, "get_bot", return_value=mock_bot), \
         patch.object(cd, "TELEGRAM_CLIENT_CHANNEL_ID", ""):
        result = await cd.post_report_to_channel("Msg")
    assert result["ok"] is False
    assert "TELEGRAM_CLIENT_CHANNEL_ID" in result["error"]
    mock_bot.send_message.assert_not_awaited()
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `.venv/bin/python -m pytest tests/test_channel_delivery.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'bot.channel_delivery'`

- [ ] **Step 3: Implementar**

Criar `webhook/bot/channel_delivery.py`:

```python
"""Posting client reports to the private Telegram channel.

One post reaches every channel subscriber — no per-user loop, no ban
risk. Mirrors the never-raise posture of execution/core/event_bus.py
sinks: failures come back as a status dict, never as an exception.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re

from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import BufferedInputFile

from bot.config import get_bot, TELEGRAM_CLIENT_CHANNEL_ID

logger = logging.getLogger(__name__)

MAX_FLOOD_RETRIES = 3
# Telegram caps message text at 4096 chars counting HTML tags; converting
# adds tag overhead, so we truncate the raw input with headroom first.
RAW_TEXT_LIMIT = 3500
TELEGRAM_CAPTION_LIMIT = 1024

# WhatsApp-style markers produced by the Curator prompt. Paired, same-line
# (except ``` blocks), no whitespace hugging the marker — unbalanced or
# intra-word markers fall through and render literally.
_PRE_RE = re.compile(r"```(.+?)```", re.DOTALL)
_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*(\S(?:[^*\n]*\S)?)\*")
_ITALIC_RE = re.compile(r"(?<![\w&])_(\S(?:[^_\n]*\S)?)_(?![\w;])")


def escape_html(text: str) -> str:
    """Escape &, <, > for parse_mode=HTML. Quotes stay readable."""
    return html.escape(text, quote=False)


def to_telegram_html(text: str) -> str:
    """Escape HTML, then convert WhatsApp markers to Telegram HTML tags.

    ```x``` → <pre>x</pre>, `x` → <code>x</code>, *x* → <b>x</b>,
    _x_ → <i>x</i>. Conversion is deterministic and per-pair: a stray
    marker stays literal instead of breaking the whole post.
    """
    escaped = escape_html(text)
    with_pre = _PRE_RE.sub(r"<pre>\1</pre>", escaped)
    with_code = _CODE_RE.sub(r"<code>\1</code>", with_pre)
    with_bold = _BOLD_RE.sub(r"<b>\1</b>", with_code)
    return _ITALIC_RE.sub(r"<i>\1</i>", with_bold)


async def _call_with_flood_retry(coro_factory):
    """Await coro_factory(); on TelegramRetryAfter sleep retry_after and retry
    (up to MAX_FLOOD_RETRIES attempts total). Re-raises the last error."""
    for attempt in range(MAX_FLOOD_RETRIES):
        try:
            return await coro_factory()
        except TelegramRetryAfter as exc:
            if attempt == MAX_FLOOD_RETRIES - 1:
                raise
            logger.warning(f"channel flood-wait: sleeping {exc.retry_after}s")
            await asyncio.sleep(exc.retry_after)


async def post_report_to_channel(
    message: str,
    pdf: bytes | None = None,
    pdf_filename: str = "report.pdf",
    *,
    silent: bool = False,
    pin: bool = False,
) -> dict:
    """Post a client report to TELEGRAM_CLIENT_CHANNEL_ID. Never raises.

    Returns {"ok": bool, "message_id": int | None, "error": str | None}.
    A PDF send failure after a successful text post keeps ok=True and
    records the problem in "error" (spec §5: PDF must not block the summary).
    """
    if not TELEGRAM_CLIENT_CHANNEL_ID:
        logger.error("TELEGRAM_CLIENT_CHANNEL_ID not set — channel post skipped")
        return {
            "ok": False,
            "message_id": None,
            "error": "TELEGRAM_CLIENT_CHANNEL_ID not set",
        }

    bot = get_bot()
    text = to_telegram_html(message[:RAW_TEXT_LIMIT])

    try:
        sent = await _call_with_flood_retry(lambda: bot.send_message(
            TELEGRAM_CLIENT_CHANNEL_ID,
            text,
            parse_mode="HTML",
            disable_notification=silent,
        ))
    except Exception as exc:
        logger.error(f"post_report_to_channel send_message failed: {exc}")
        return {"ok": False, "message_id": None, "error": str(exc)[:300]}

    result = {"ok": True, "message_id": sent.message_id, "error": None}

    if pdf is not None:
        try:
            doc = BufferedInputFile(pdf, filename=pdf_filename)
            await _call_with_flood_retry(lambda: bot.send_document(
                TELEGRAM_CLIENT_CHANNEL_ID,
                doc,
                caption=escape_html(pdf_filename)[:TELEGRAM_CAPTION_LIMIT],
                disable_notification=True,
            ))
        except Exception as exc:
            logger.error(f"post_report_to_channel send_document failed: {exc}")
            result = {**result, "error": f"pdf_send_failed: {str(exc)[:200]}"}

    if pin:
        try:
            await _call_with_flood_retry(lambda: bot.pin_chat_message(
                TELEGRAM_CLIENT_CHANNEL_ID,
                sent.message_id,
                disable_notification=True,
            ))
        except Exception as exc:
            logger.warning(f"pin_chat_message failed: {exc}")

    return result
```

- [ ] **Step 4: Rodar e confirmar verde**

Run: `.venv/bin/python -m pytest tests/test_channel_delivery.py -v`
Expected: 12 passed

Nota: se `TelegramRetryAfter(method=MagicMock(), message="flood", retry_after=0)` falhar na construção (varia por versão do aiogram), inspecionar a assinatura com `.venv/bin/python -c "import inspect, aiogram.exceptions as e; print(inspect.signature(e.TelegramRetryAfter.__init__))"` e ajustar o helper `_retry_after` do teste — não o código de produção.

- [ ] **Step 5: Commit**

```bash
git add webhook/bot/channel_delivery.py tests/test_channel_delivery.py
git commit -m "feat(bot): channel_delivery — post de relatório no canal privado com retry de flood-wait"
```

---

### Task 3: `/store-draft` roteia cliente → canal

**Files:**
- Modify: `webhook/routes/api.py:98-107` (bloco "Telegram delivery to subscribers")
- Test: `tests/test_store_draft_delivery_routing.py`

**Interfaces:**
- Consumes: `bot.routing.resolve_destination` / `DEST_CLIENT_CHANNEL` (Task 1), `bot.channel_delivery.post_report_to_channel` (Task 2), `bot.delivery.deliver_to_subscribers` (existente).
- Produces: comportamento HTTP — resposta do `/store-draft` mantém a chave `telegram_delivery` (agora com o dict do canal para workflows de cliente).

- [ ] **Step 1: Escrever os testes que falham**

Criar `tests/test_store_draft_delivery_routing.py`:

```python
"""Store-draft routes client workflows to the channel, internal to DMs."""
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

import pytest


class _FakeRequest:
    def __init__(self, payload: dict):
        self._payload = payload

    async def json(self):
        return self._payload


def _payload(workflow_type: str) -> dict:
    return {
        "draft_id": "d1",
        "message": "conteúdo",
        "workflow_type": workflow_type,
        "direct_delivery": True,
    }


@pytest.mark.asyncio
async def test_client_workflow_posts_to_channel():
    from routes.api import store_draft
    channel_mock = AsyncMock(return_value={"ok": True, "message_id": 7, "error": None})
    dm_mock = AsyncMock()
    with patch("routes.api.drafts_set"), \
         patch("bot.channel_delivery.post_report_to_channel", channel_mock), \
         patch("bot.delivery.deliver_to_subscribers", dm_mock):
        resp = await store_draft(_FakeRequest(_payload("daily_report")))
    body = json.loads(resp.body)
    channel_mock.assert_awaited_once_with("conteúdo")
    dm_mock.assert_not_awaited()
    assert body["telegram_delivery"] == {"ok": True, "message_id": 7, "error": None}


@pytest.mark.asyncio
async def test_internal_workflow_keeps_dm_broadcast():
    from routes.api import store_draft
    channel_mock = AsyncMock()
    dm_mock = AsyncMock(return_value={"sent": 2, "failed": 0, "errors": []})
    with patch("routes.api.drafts_set"), \
         patch("bot.channel_delivery.post_report_to_channel", channel_mock), \
         patch("bot.delivery.deliver_to_subscribers", dm_mock):
        resp = await store_draft(_FakeRequest(_payload("morning_check")))
    body = json.loads(resp.body)
    dm_mock.assert_awaited_once_with("morning_check", "conteúdo")
    channel_mock.assert_not_awaited()
    assert body["telegram_delivery"]["sent"] == 2


@pytest.mark.asyncio
async def test_no_direct_delivery_skips_both():
    from routes.api import store_draft
    channel_mock = AsyncMock()
    dm_mock = AsyncMock()
    payload = {**_payload("daily_report"), "direct_delivery": False}
    with patch("routes.api.drafts_set"), \
         patch("bot.channel_delivery.post_report_to_channel", channel_mock), \
         patch("bot.delivery.deliver_to_subscribers", dm_mock):
        resp = await store_draft(_FakeRequest(payload))
    body = json.loads(resp.body)
    channel_mock.assert_not_awaited()
    dm_mock.assert_not_awaited()
    assert "telegram_delivery" not in body
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `.venv/bin/python -m pytest tests/test_store_draft_delivery_routing.py -v`
Expected: `test_client_workflow_posts_to_channel` FAIL (o código atual chama `deliver_to_subscribers` para tudo); os outros dois podem passar.

- [ ] **Step 3: Implementar**

Em `webhook/routes/api.py`, substituir o bloco das linhas 98–107:

```python
    # Telegram delivery to subscribers (non-blocking)
    telegram_result = None
    if direct_delivery and workflow_type:
        from bot.delivery import deliver_to_subscribers
        try:
            telegram_result = await deliver_to_subscribers(workflow_type, message)
            logger.info(f"Telegram delivery: {telegram_result}")
        except Exception as exc:
            logger.error(f"Telegram delivery failed: {exc}")
            telegram_result = {"sent": 0, "failed": 0, "error": str(exc)}
```

por:

```python
    # Telegram delivery (non-blocking): client workflows → private channel,
    # internal workflows → DM broadcast to subscribers (legacy behavior).
    telegram_result = None
    if direct_delivery and workflow_type:
        from bot.routing import resolve_destination, DEST_CLIENT_CHANNEL
        try:
            if resolve_destination(workflow_type) == DEST_CLIENT_CHANNEL:
                from bot.channel_delivery import post_report_to_channel
                telegram_result = await post_report_to_channel(message)
            else:
                from bot.delivery import deliver_to_subscribers
                telegram_result = await deliver_to_subscribers(workflow_type, message)
            logger.info(f"Telegram delivery: {telegram_result}")
        except Exception as exc:
            logger.error(f"Telegram delivery failed: {exc}")
            telegram_result = {"sent": 0, "failed": 0, "error": str(exc)}
```

- [ ] **Step 4: Rodar e confirmar verde (+ sem regressão)**

Run: `.venv/bin/python -m pytest tests/test_store_draft_delivery_routing.py tests/test_bot_delivery.py tests/test_webhook_status.py -v`
Expected: todos passed

- [ ] **Step 5: Commit**

```bash
git add webhook/routes/api.py tests/test_store_draft_delivery_routing.py
git commit -m "feat(webhook): store-draft roteia workflows de cliente para o canal Telegram"
```

---

### Task 4: Aprovação de texto gateada pela flag (`dispatch.py`)

Os 3 call sites de `callbacks_curation.py` (linhas 159, 302, 331) chamam `process_approval_async` — gatear **dentro** dela cobre todos de uma vez. Todo conteúdo que passa por essa função é conteúdo de cliente (drafts curados / market news), então a flag global basta.

**Files:**
- Modify: `webhook/dispatch.py` (topo de `process_approval_async` linha 126 e de `process_test_send_async` linha 257; novo helper `_post_approval_to_channel`)
- Modify: `tests/test_dispatch_idempotency.py` (fixture autouse forçando modo `uazapi` — os testes existentes exercitam o caminho WhatsApp)
- Test: `tests/test_dispatch_channel_mode.py`

**Interfaces:**
- Consumes: `bot.routing.client_delivery_mode` (Task 1), `bot.channel_delivery.post_report_to_channel` (Task 2), `get_bot` / `build_approval_keyboard` (já importados em dispatch.py).
- Produces: mesma assinatura pública (`process_approval_async(chat_id, draft_message, draft_id, uazapi_token=None, uazapi_url=None)`); nenhum caller muda.

- [ ] **Step 1: Escrever os testes que falham**

Criar `tests/test_dispatch_channel_mode.py`:

```python
"""CLIENT_DELIVERY_CHANNEL=telegram routes approvals to the channel, not uazapi."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

import pytest


@pytest.fixture
def mock_bot():
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    return bot


@pytest.mark.asyncio
async def test_approval_telegram_mode_posts_to_channel(monkeypatch, mock_bot):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "telegram")
    channel_mock = AsyncMock(return_value={"ok": True, "message_id": 5, "error": None})
    contacts_mock = AsyncMock()
    with patch("dispatch.get_bot", return_value=mock_bot), \
         patch("bot.channel_delivery.post_report_to_channel", channel_mock), \
         patch("dispatch.get_contacts", contacts_mock):
        from dispatch import process_approval_async
        await process_approval_async(999, "Relatório do dia", "draft-1")
    channel_mock.assert_awaited_once_with("Relatório do dia")
    contacts_mock.assert_not_awaited()  # WhatsApp path never touched
    # Admin got a confirmation message
    confirmations = [c.args[1] for c in mock_bot.send_message.await_args_list]
    assert any("canal" in text.lower() for text in confirmations)


@pytest.mark.asyncio
async def test_approval_telegram_mode_reports_failure(monkeypatch, mock_bot):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "telegram")
    channel_mock = AsyncMock(return_value={"ok": False, "message_id": None, "error": "no channel"})
    with patch("dispatch.get_bot", return_value=mock_bot), \
         patch("bot.channel_delivery.post_report_to_channel", channel_mock):
        from dispatch import process_approval_async
        await process_approval_async(999, "Relatório", "draft-2")
    confirmations = [c.args[1] for c in mock_bot.send_message.await_args_list]
    assert any("❌" in text for text in confirmations)


@pytest.mark.asyncio
async def test_approval_uazapi_mode_keeps_whatsapp_path(monkeypatch, mock_bot):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "uazapi")
    channel_mock = AsyncMock()
    contacts_mock = AsyncMock(return_value=[])  # empty list → fan-out no-ops
    with patch("dispatch.get_bot", return_value=mock_bot), \
         patch("bot.channel_delivery.post_report_to_channel", channel_mock), \
         patch("dispatch.get_contacts", contacts_mock):
        from dispatch import process_approval_async
        await process_approval_async(999, "Relatório", "draft-3")
    contacts_mock.assert_awaited_once()
    channel_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_test_send_telegram_mode_previews_to_admin(monkeypatch, mock_bot):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "telegram")
    contacts_mock = AsyncMock()
    with patch("dispatch.get_bot", return_value=mock_bot), \
         patch("dispatch.get_contacts", contacts_mock):
        from dispatch import process_test_send_async
        await process_test_send_async(999, "draft-4", "Corpo do relatório")
    contacts_mock.assert_not_awaited()
    args, kwargs = mock_bot.send_message.await_args
    assert args[0] == 999
    assert "PREVIEW" in args[1]
    assert kwargs.get("reply_markup") is not None  # approval keyboard attached
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `.venv/bin/python -m pytest tests/test_dispatch_channel_mode.py -v`
Expected: FAIL — os testes de modo telegram vão cair no caminho WhatsApp (chamam `get_contacts`).

- [ ] **Step 3: Implementar**

Em `webhook/dispatch.py`, adicionar helper antes de `process_approval_async` (após a definição de `send_whatsapp`):

```python
# ── Telegram channel path (CLIENT_DELIVERY_CHANNEL=telegram) ──

async def _post_approval_to_channel(chat_id, draft_message):
    """Post the approved draft to the private client channel and confirm to admin."""
    from bot.channel_delivery import post_report_to_channel
    bot = get_bot()
    result = await post_report_to_channel(draft_message)
    if result["ok"]:
        note = " (aviso: PDF/extra falhou)" if result["error"] else ""
        await bot.send_message(
            chat_id, f"✅ Publicado no canal do Telegram{note}.",
        )
    else:
        await bot.send_message(
            chat_id, f"❌ Falha ao publicar no canal: {result['error']}",
        )
```

No **início do corpo** de `process_approval_async` (linha 126, antes de `bot = get_bot()`):

```python
async def process_approval_async(chat_id, draft_message, draft_id, uazapi_token=None, uazapi_url=None):
    """Broadcast an approved draft.

    CLIENT_DELIVERY_CHANNEL=telegram (default): single post to the private
    client channel. 'uazapi': legacy WhatsApp fan-out (rollback path).
    """
    from bot.routing import client_delivery_mode
    if client_delivery_mode() == "telegram":
        await _post_approval_to_channel(chat_id, draft_message)
        return
    # ── legacy uazapi path below (unchanged) ──
    bot = get_bot()
    ...
```

No **início do corpo** de `process_test_send_async` (linha 257):

```python
async def process_test_send_async(chat_id, draft_id, draft_message, uazapi_token=None, uazapi_url=None):
    """Send message only to the first contact for testing.

    In telegram mode there is no per-contact test — show the admin a preview
    with the approval keyboard instead.
    """
    from bot.routing import client_delivery_mode
    bot = get_bot()
    if client_delivery_mode() == "telegram":
        display = draft_message[:3500] if len(draft_message) > 3500 else draft_message
        await bot.send_message(
            chat_id,
            f"🧪 *PREVIEW (vai para o canal Telegram)*\n\n{display}",
            reply_markup=build_approval_keyboard(draft_id),
        )
        return
    try:
        contacts = await get_contacts()
        ...
```

(O `bot = get_bot()` que existia dentro do `try` original passa a ficar antes do `if`, como acima; o resto do corpo permanece intacto.)

Em `tests/test_dispatch_idempotency.py`, adicionar após os imports do topo:

```python
@pytest.fixture(autouse=True)
def _legacy_uazapi_mode(monkeypatch):
    """These tests exercise the legacy WhatsApp fan-out path."""
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "uazapi")
```

- [ ] **Step 4: Rodar e confirmar verde (+ sem regressão)**

Run: `.venv/bin/python -m pytest tests/test_dispatch_channel_mode.py tests/test_dispatch_idempotency.py tests/test_callbacks_curation.py -v`
Expected: todos passed

Nota: se `test_approval_uazapi_mode_keeps_whatsapp_path` falhar por efeito colateral do `DeliveryReporter` real (Redis/env), adicionar ao `with` do teste: `patch("dispatch.DeliveryReporter", MagicMock())` — o assert relevante é `get_contacts` ter sido chamado e o canal não.

- [ ] **Step 5: Commit**

```bash
git add webhook/dispatch.py tests/test_dispatch_channel_mode.py tests/test_dispatch_idempotency.py
git commit -m "feat(webhook): aprovação de draft publica no canal Telegram (flag CLIENT_DELIVERY_CHANNEL)"
```

---

### Task 5: PDF OneDrive → canal (`dispatch_document.py`)

**Files:**
- Modify: `webhook/dispatch_document.py` (branch no início de `dispatch_document`, linha ~136 após o refresh de URL; novo helper `_dispatch_to_channel`)
- Modify: `tests/test_dispatch_document.py` (fixture autouse modo `uazapi`)
- Test: `tests/test_dispatch_document_channel.py`

**Interfaces:**
- Consumes: `bot.routing.client_delivery_mode` (Task 1), `bot.channel_delivery.post_report_to_channel` (Task 2), helpers existentes `_claim_idempotency`, `EventBus`, `requests`.
- Produces: `dispatch_document()` mantém assinatura e formato de retorno `{"sent", "failed", "skipped", "errors"}` — `callbacks_onedrive.py` não muda (o card de resultado continua funcionando).

- [ ] **Step 1: Escrever os testes que falham**

Criar `tests/test_dispatch_document_channel.py`:

```python
"""dispatch_document in telegram mode posts the PDF once to the channel."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

import pytest
import fakeredis.aioredis


@pytest.fixture(autouse=True)
def _telegram_mode(monkeypatch):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "telegram")


@pytest.fixture
def redis_client():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def fresh_approval_state():
    return {
        "drive_id": "drive-test",
        "drive_item_id": "item-abc",
        "filename": "Minerals_Report.pdf",
        "size": 1024,
        "downloadUrl": "https://cdn.example.com/fresh?sig=x",
        "downloadUrl_fetched_at": datetime.now(timezone.utc).isoformat(),
        "status": "dispatching",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


@pytest.fixture(autouse=True)
def mock_pdf_download():
    fake_resp = MagicMock()
    fake_resp.content = b"%PDF-1.4 fake-pdf-bytes"
    fake_resp.raise_for_status = MagicMock()
    with patch("dispatch_document.requests.get", return_value=fake_resp) as p:
        yield p


@pytest.mark.asyncio
async def test_telegram_mode_posts_pdf_to_channel(redis_client, fresh_approval_state):
    from dispatch_document import dispatch_document
    await redis_client.set("approval:abc12", json.dumps(fresh_approval_state))
    channel_mock = AsyncMock(return_value={"ok": True, "message_id": 9, "error": None})
    uazapi_cls = MagicMock()
    with patch("bot.channel_delivery.post_report_to_channel", channel_mock), \
         patch("dispatch_document.UazapiClient", uazapi_cls), \
         patch("dispatch_document._redis", return_value=redis_client):
        result = await dispatch_document("abc12", "minerals_report")
    channel_mock.assert_awaited_once()
    _, kwargs = channel_mock.await_args
    assert kwargs["pdf"] == b"%PDF-1.4 fake-pdf-bytes"
    assert kwargs["pdf_filename"] == "Minerals_Report.pdf"
    uazapi_cls.assert_not_called()
    assert result == {"sent": 1, "failed": 0, "skipped": 0, "errors": []}


@pytest.mark.asyncio
async def test_telegram_mode_idempotent_second_run_skips(redis_client, fresh_approval_state):
    from dispatch_document import dispatch_document
    await redis_client.set("approval:abc12", json.dumps(fresh_approval_state))
    channel_mock = AsyncMock(return_value={"ok": True, "message_id": 9, "error": None})
    with patch("bot.channel_delivery.post_report_to_channel", channel_mock), \
         patch("dispatch_document._redis", return_value=redis_client):
        await dispatch_document("abc12", "minerals_report")
        result = await dispatch_document("abc12", "minerals_report")
    assert channel_mock.await_count == 1
    assert result["skipped"] == 1
    assert result["sent"] == 0


@pytest.mark.asyncio
async def test_telegram_mode_channel_failure_counts_failed(redis_client, fresh_approval_state):
    from dispatch_document import dispatch_document
    await redis_client.set("approval:abc12", json.dumps(fresh_approval_state))
    channel_mock = AsyncMock(return_value={"ok": False, "message_id": None, "error": "not admin"})
    with patch("bot.channel_delivery.post_report_to_channel", channel_mock), \
         patch("dispatch_document._redis", return_value=redis_client):
        result = await dispatch_document("abc12", "minerals_report")
    assert result["sent"] == 0
    assert result["failed"] == 1
    assert result["errors"][0]["error"] == "not admin"
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `.venv/bin/python -m pytest tests/test_dispatch_document_channel.py -v`
Expected: FAIL — o código atual faz fan-out uazapi (vai tentar `ContactsRepo` real ou falhar em `UazapiClient`).

- [ ] **Step 3: Implementar**

Em `webhook/dispatch_document.py`:

1. Adicionar import no topo (junto aos outros):

```python
from bot.routing import client_delivery_mode
```

2. Adicionar helper antes de `dispatch_document`:

```python
async def _dispatch_to_channel(
    redis_client,
    approval_id: str,
    state: dict,
    trace_id: str | None,
) -> dict:
    """Single post to the private Telegram channel (CLIENT_DELIVERY_CHANNEL=telegram).

    Replaces the per-contact uazapi fan-out: one send_document reaches every
    channel subscriber. Idempotency claims the pseudo-recipient
    'telegram_channel' so a double-click can't double-post.
    """
    from bot.channel_delivery import post_report_to_channel

    bus = EventBus(workflow="onedrive_webhook", trace_id=trace_id or state.get("trace_id"))
    bus.emit("dispatch_started", detail={
        "approval_id": approval_id,
        "list_code": "telegram_channel",
        "recipients": 1,
    })

    claimed = await _claim_idempotency(
        redis_client, "telegram_channel", state["drive_item_id"]
    )
    if not claimed:
        bus.emit("dispatch_completed", detail={
            "approval_id": approval_id, "sent": 0, "failed": 0, "skipped": 1,
        })
        return {"sent": 0, "failed": 0, "skipped": 1, "errors": []}

    def _download_pdf() -> bytes:
        r = requests.get(state["downloadUrl"], timeout=60, stream=False)
        r.raise_for_status()
        return r.content

    try:
        pdf_bytes = await asyncio.to_thread(_download_pdf)
        bus.emit("pdf_downloaded", detail={
            "approval_id": approval_id, "bytes": len(pdf_bytes),
        })
    except Exception as exc:
        bus.emit("pdf_download_failed", level="error", detail={
            "approval_id": approval_id, "error": str(exc)[:300],
        })
        bus.emit("dispatch_completed", detail={
            "approval_id": approval_id, "sent": 0, "failed": 1, "skipped": 0,
        })
        return {
            "sent": 0, "failed": 1, "skipped": 0,
            "errors": [{"phone": "telegram_channel", "error": f"download: {str(exc)[:250]}"}],
        }

    result = await post_report_to_channel(
        f"📄 {state['filename']}",
        pdf=pdf_bytes,
        pdf_filename=state["filename"],
    )
    sent, failed = (1, 0) if result["ok"] else (0, 1)
    errors = [] if result["ok"] else [
        {"phone": "telegram_channel", "error": result["error"] or "unknown"}
    ]
    bus.emit("dispatch_completed", detail={
        "approval_id": approval_id, "sent": sent, "failed": failed, "skipped": 0,
    })
    return {"sent": sent, "failed": failed, "skipped": 0, "errors": errors}
```

3. Em `dispatch_document()`, logo **após** o bloco de refresh do downloadUrl (após a linha `state = await _refresh_download_url(...)` e antes de `contacts_repo = ContactsRepo()`), inserir:

```python
    if client_delivery_mode() == "telegram":
        return await _dispatch_to_channel(redis_client, approval_id, state, trace_id)
```

4. Em `tests/test_dispatch_document.py`, adicionar após os imports:

```python
@pytest.fixture(autouse=True)
def _legacy_uazapi_mode(monkeypatch):
    """These tests exercise the legacy uazapi fan-out path."""
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "uazapi")
```

- [ ] **Step 4: Rodar e confirmar verde (+ sem regressão)**

Run: `.venv/bin/python -m pytest tests/test_dispatch_document_channel.py tests/test_dispatch_document.py tests/test_dispatch_document_throttle.py -v`
Expected: todos passed

- [ ] **Step 5: Commit**

```bash
git add webhook/dispatch_document.py tests/test_dispatch_document_channel.py tests/test_dispatch_document.py
git commit -m "feat(webhook): PDF OneDrive publica no canal Telegram em modo telegram"
```

---

### Task 6: Comando `/convite` (invite link + QR)

**Files:**
- Modify: `webhook/bot/routers/commands.py` (novo comando no `admin_router` + helper de QR; adicionar `timedelta` ao import de datetime e `BufferedInputFile` aos imports aiogram)
- Modify: `webhook/routes/api.py:162-176` (registrar `/convite` na lista de `setMyCommands`)
- Modify: `webhook/requirements.txt` (dep nova)
- Test: `tests/test_channel_invite.py`

**Interfaces:**
- Consumes: `bot.config.TELEGRAM_CLIENT_CHANNEL_ID` (Task 1), `get_bot` (já importado em commands.py).
- Produces: comando `/convite` (admin) que responde com foto QR + link; sem API consumida por outras tasks.

- [ ] **Step 1: Instalar a dependência**

```bash
uv pip install --python .venv/bin/python 'qrcode[pil]>=7.4,<9.0'
```

Adicionar ao final de `webhook/requirements.txt`:

```
qrcode[pil]>=7.4,<9.0
```

(Somente `webhook/requirements.txt`: o bot roda no Railway a partir dele; o `requirements.txt` da raiz serve o GH Actions, que não gera convite.)

- [ ] **Step 2: Escrever os testes que falham**

Criar `tests/test_channel_invite.py`:

```python
"""Admin /convite command: invite link with join request + QR photo."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

import pytest


@pytest.fixture
def mock_message():
    msg = MagicMock()
    msg.chat.id = 999
    msg.answer = AsyncMock()
    msg.answer_photo = AsyncMock()
    return msg


@pytest.fixture
def mock_bot():
    bot = AsyncMock()
    link = MagicMock()
    link.invite_link = "https://t.me/+AbCdEf123"
    bot.create_chat_invite_link = AsyncMock(return_value=link)
    return bot


@pytest.mark.asyncio
async def test_convite_creates_join_request_link_and_qr(mock_message, mock_bot):
    import bot.routers.commands as cmds
    with patch.object(cmds, "get_bot", return_value=mock_bot), \
         patch("bot.config.TELEGRAM_CLIENT_CHANNEL_ID", "-1001234"):
        await cmds.cmd_convite(mock_message)
    _, kwargs = mock_bot.create_chat_invite_link.await_args
    assert kwargs["chat_id"] == "-1001234"
    assert kwargs["creates_join_request"] is True
    assert "member_limit" not in kwargs  # mutually exclusive with join requests
    mock_message.answer_photo.assert_awaited_once()
    _, photo_kwargs = mock_message.answer_photo.await_args
    assert "https://t.me/+AbCdEf123" in photo_kwargs["caption"]


@pytest.mark.asyncio
async def test_convite_without_channel_configured(mock_message, mock_bot):
    import bot.routers.commands as cmds
    with patch.object(cmds, "get_bot", return_value=mock_bot), \
         patch("bot.config.TELEGRAM_CLIENT_CHANNEL_ID", ""):
        await cmds.cmd_convite(mock_message)
    mock_bot.create_chat_invite_link.assert_not_awaited()
    args, _ = mock_message.answer.await_args
    assert "TELEGRAM_CLIENT_CHANNEL_ID" in args[0]


def test_qr_png_bytes_returns_png():
    from bot.routers.commands import _qr_png_bytes
    data = _qr_png_bytes("https://t.me/+AbCdEf123")
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
```

- [ ] **Step 3: Rodar e confirmar falha**

Run: `.venv/bin/python -m pytest tests/test_channel_invite.py -v`
Expected: FAIL com `AttributeError: ... has no attribute 'cmd_convite'`

- [ ] **Step 4: Implementar**

Em `webhook/bot/routers/commands.py`:

1. Ajustar imports no topo:
   - trocar `from datetime import datetime, timezone` por `from datetime import datetime, timedelta, timezone`
   - trocar `from aiogram.types import Message` por `from aiogram.types import BufferedInputFile, Message`

2. Adicionar ao final da seção de comandos do `admin_router` (após o handler de `/s`, linha ~305):

```python
# ── /convite — invite link do canal de clientes + QR ──


def _qr_png_bytes(url: str) -> bytes:
    """Render url as a QR code PNG (bytes)."""
    import io

    import qrcode

    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@admin_router.message(Command("convite"))
async def cmd_convite(message: Message):
    """Generate a 7-day join-request invite link + QR for the client channel.

    member_limit is intentionally absent: Telegram's API forbids combining it
    with creates_join_request, and per-join admin approval is the control.
    """
    from bot.config import TELEGRAM_CLIENT_CHANNEL_ID
    if not TELEGRAM_CLIENT_CHANNEL_ID:
        await message.answer(
            "❌ TELEGRAM_CLIENT_CHANNEL_ID não configurado.\n"
            "Crie o canal, adicione o bot como admin e configure a env var."
        )
        return

    bot = get_bot()
    now = datetime.now(timezone.utc)
    try:
        link = await bot.create_chat_invite_link(
            chat_id=TELEGRAM_CLIENT_CHANNEL_ID,
            name=f"convite {now:%Y-%m-%d}",
            expire_date=now + timedelta(days=7),
            creates_join_request=True,
        )
    except Exception as exc:
        logger.error(f"create_chat_invite_link failed: {exc}")
        await message.answer(
            f"❌ Falha ao gerar convite: {str(exc)[:200]}\n"
            "Confira se o bot é admin do canal com permissão de convidar."
        )
        return

    qr_png = _qr_png_bytes(link.invite_link)
    await message.answer_photo(
        BufferedInputFile(qr_png, filename="convite-canal.png"),
        caption=(
            f"🔗 {link.invite_link}\n\n"
            f"Validade: 7 dias · entrada por aprovação (join request).\n"
            f"Cada pedido chega aqui como card pra você aprovar."
        ),
    )
    logger.info("channel invite link generated")
```

3. Em `webhook/routes/api.py`, na lista `commands` do handler `register_commands` (linha ~162), adicionar:

```python
        {"command": "convite", "description": "Gerar convite + QR do canal de clientes"},
```

- [ ] **Step 5: Rodar e confirmar verde**

Run: `.venv/bin/python -m pytest tests/test_channel_invite.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add webhook/bot/routers/commands.py webhook/routes/api.py webhook/requirements.txt tests/test_channel_invite.py
git commit -m "feat(bot): /convite gera invite link com join request + QR do canal de clientes"
```

---

### Task 7: Join requests do canal (card de aprovação do admin)

**Files:**
- Create: `webhook/bot/routers/channel_join.py`
- Modify: `webhook/bot/callback_data.py` (nova classe ao final)
- Modify: `webhook/bot/keyboards.py` (novo builder ao final + import da classe nova)
- Modify: `webhook/bot/main.py` (incluir router + `allowed_updates` no `set_webhook`)
- Test: `tests/test_channel_join.py`

**Interfaces:**
- Consumes: `bot.config.get_bot` / `TELEGRAM_CLIENT_CHANNEL_ID`, `bot.users.ADMIN_CHAT_ID` / `is_admin` / `format_user_label` (existentes).
- Produces:
  - `callback_data.ChannelJoinApproval(CallbackData, prefix="chjoin")` com `action: str` (`approve`|`decline`) e `user_id: int`
  - `keyboards.build_channel_join_keyboard(user_id: int) -> InlineKeyboardMarkup`
  - `channel_join.channel_join_router` (registrado em main.py)

**Gotcha crítico:** por padrão o Telegram **não envia** updates `chat_join_request` ao webhook. O `set_webhook` em `main.py:63` precisa passar `allowed_updates=dp.resolve_used_update_types()` — sem isso o handler nunca dispara em produção.

- [ ] **Step 1: Escrever os testes que falham**

Criar `tests/test_channel_join.py`:

```python
"""Channel join requests: admin card + approve/decline callbacks."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

import pytest


@pytest.fixture
def mock_bot():
    bot = AsyncMock()
    bot.send_message = AsyncMock()
    bot.edit_message_text = AsyncMock()
    bot.approve_chat_join_request = AsyncMock()
    bot.decline_chat_join_request = AsyncMock()
    return bot


def _join_request(chat_id: str = "-1001234", user_id: int = 555):
    req = MagicMock()
    req.chat.id = int(chat_id)
    req.from_user.id = user_id
    req.from_user.full_name = "Cliente Teste"
    req.from_user.username = "cliente"
    req.from_user.first_name = "Cliente"
    return req


def _callback(user_id: int = 777, data: str = ""):
    cb = MagicMock()
    cb.from_user.id = user_id
    cb.message.chat.id = 999
    cb.message.message_id = 1
    cb.answer = AsyncMock()
    return cb


@pytest.mark.asyncio
async def test_join_request_notifies_admin(mock_bot):
    import bot.routers.channel_join as cj
    with patch.object(cj, "get_bot", return_value=mock_bot), \
         patch.object(cj, "TELEGRAM_CLIENT_CHANNEL_ID", "-1001234"), \
         patch.object(cj, "ADMIN_CHAT_ID", 999):
        await cj.on_join_request(_join_request())
    args, kwargs = mock_bot.send_message.await_args
    assert args[0] == 999
    assert "555" in args[1]
    assert kwargs["reply_markup"] is not None


@pytest.mark.asyncio
async def test_join_request_other_chat_ignored(mock_bot):
    import bot.routers.channel_join as cj
    with patch.object(cj, "get_bot", return_value=mock_bot), \
         patch.object(cj, "TELEGRAM_CLIENT_CHANNEL_ID", "-1001234"), \
         patch.object(cj, "ADMIN_CHAT_ID", 999):
        await cj.on_join_request(_join_request(chat_id="-1009999"))
    mock_bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_approve_calls_api_and_updates_card(mock_bot):
    import bot.routers.channel_join as cj
    from bot.callback_data import ChannelJoinApproval
    cb_data = ChannelJoinApproval(action="approve", user_id=555)
    with patch.object(cj, "get_bot", return_value=mock_bot), \
         patch.object(cj, "TELEGRAM_CLIENT_CHANNEL_ID", "-1001234"), \
         patch("bot.routers.channel_join.is_admin", return_value=True):
        await cj.on_join_decision(_callback(), cb_data)
    mock_bot.approve_chat_join_request.assert_awaited_once_with("-1001234", 555)
    mock_bot.edit_message_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_decline_calls_api(mock_bot):
    import bot.routers.channel_join as cj
    from bot.callback_data import ChannelJoinApproval
    cb_data = ChannelJoinApproval(action="decline", user_id=555)
    with patch.object(cj, "get_bot", return_value=mock_bot), \
         patch.object(cj, "TELEGRAM_CLIENT_CHANNEL_ID", "-1001234"), \
         patch("bot.routers.channel_join.is_admin", return_value=True):
        await cj.on_join_decision(_callback(), cb_data)
    mock_bot.decline_chat_join_request.assert_awaited_once_with("-1001234", 555)


@pytest.mark.asyncio
async def test_non_admin_cannot_decide(mock_bot):
    import bot.routers.channel_join as cj
    from bot.callback_data import ChannelJoinApproval
    cb_data = ChannelJoinApproval(action="approve", user_id=555)
    cb = _callback()
    with patch.object(cj, "get_bot", return_value=mock_bot), \
         patch("bot.routers.channel_join.is_admin", return_value=False):
        await cj.on_join_decision(cb, cb_data)
    mock_bot.approve_chat_join_request.assert_not_awaited()
    cb.answer.assert_awaited_once()
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `.venv/bin/python -m pytest tests/test_channel_join.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'bot.routers.channel_join'`

- [ ] **Step 3: Implementar**

1. Em `webhook/bot/callback_data.py`, ao final:

```python
class ChannelJoinApproval(CallbackData, prefix="chjoin"):
    action: str  # approve, decline
    user_id: int
```

2. Em `webhook/bot/keyboards.py`: adicionar `ChannelJoinApproval` ao import de `bot.callback_data` e, ao final do arquivo:

```python
def build_channel_join_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Approve/decline card for a client-channel join request."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Aprovar entrada",
            callback_data=ChannelJoinApproval(action="approve", user_id=user_id).pack(),
        ),
        InlineKeyboardButton(
            text="❌ Recusar",
            callback_data=ChannelJoinApproval(action="decline", user_id=user_id).pack(),
        ),
    )
    return builder.as_markup()
```

3. Criar `webhook/bot/routers/channel_join.py`:

```python
"""Join requests for the private client channel.

Telegram delivers a chat_join_request update when someone opens the
/convite link. The admin gets an approve/decline card; approving calls
approve_chat_join_request. Requires allowed_updates to include
'chat_join_request' in set_webhook (see main.py).
"""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import CallbackQuery, ChatJoinRequest

from bot.callback_data import ChannelJoinApproval
from bot.config import get_bot, TELEGRAM_CLIENT_CHANNEL_ID
from bot.keyboards import build_channel_join_keyboard
from bot.users import ADMIN_CHAT_ID, format_user_label, is_admin

logger = logging.getLogger(__name__)

channel_join_router = Router(name="channel_join")


@channel_join_router.chat_join_request()
async def on_join_request(request: ChatJoinRequest):
    if str(request.chat.id) != str(TELEGRAM_CLIENT_CHANNEL_ID):
        return
    user = request.from_user
    if not ADMIN_CHAT_ID:
        logger.warning("join request received but ADMIN_CHAT_ID unset")
        return
    bot = get_bot()
    label = format_user_label(user)
    await bot.send_message(
        ADMIN_CHAT_ID,
        f"🔔 *Pedido de entrada no canal*\n\n"
        f"Nome: {user.full_name}\n"
        f"User: {label}\n"
        f"ID: `{user.id}`",
        reply_markup=build_channel_join_keyboard(user.id),
    )
    logger.info(f"channel join request from {user.id}")


@channel_join_router.callback_query(ChannelJoinApproval.filter())
async def on_join_decision(query: CallbackQuery, callback_data: ChannelJoinApproval):
    if not is_admin(query.from_user.id):
        await query.answer("Nao autorizado")
        return

    bot = get_bot()
    user_id = callback_data.user_id

    if callback_data.action == "approve":
        try:
            await bot.approve_chat_join_request(TELEGRAM_CLIENT_CHANNEL_ID, user_id)
        except Exception as exc:
            logger.error(f"approve_chat_join_request failed: {exc}")
            await query.answer(f"❌ {str(exc)[:60]}", show_alert=True)
            return
        await query.answer("✅ Aprovado")
        await bot.edit_message_text(
            f"✅ *Entrada aprovada* — `{user_id}`",
            chat_id=query.message.chat.id,
            message_id=query.message.message_id,
            reply_markup=None,
        )
        logger.info(f"channel join approved for {user_id}")
    else:
        try:
            await bot.decline_chat_join_request(TELEGRAM_CLIENT_CHANNEL_ID, user_id)
        except Exception as exc:
            logger.error(f"decline_chat_join_request failed: {exc}")
            await query.answer(f"❌ {str(exc)[:60]}", show_alert=True)
            return
        await query.answer("❌ Recusado")
        await bot.edit_message_text(
            f"❌ *Entrada recusada* — `{user_id}`",
            chat_id=query.message.chat.id,
            message_id=query.message.message_id,
            reply_markup=None,
        )
        logger.info(f"channel join declined for {user_id}")
```

4. Em `webhook/bot/main.py`:

- Adicionar import (junto aos outros routers, após linha 30):

```python
from bot.routers.channel_join import channel_join_router
```

- Em `create_app()`, incluir o router logo após `onboarding_router` (linha 88):

```python
    dp.include_router(channel_join_router)  # chat_join_request do canal de clientes
```

- Em `on_startup()` (linha 61-64), trocar:

```python
async def on_startup(app: web.Application):
    bot = get_bot()
    webhook_url = f"{TELEGRAM_WEBHOOK_URL}{WEBHOOK_PATH}"
    await bot.set_webhook(webhook_url)
```

por:

```python
async def on_startup(app: web.Application):
    bot = get_bot()
    dp = get_dispatcher()
    webhook_url = f"{TELEGRAM_WEBHOOK_URL}{WEBHOOK_PATH}"
    # allowed_updates from registered handlers — without this Telegram
    # never delivers chat_join_request updates to the webhook.
    await bot.set_webhook(
        webhook_url, allowed_updates=dp.resolve_used_update_types(),
    )
```

- [ ] **Step 4: Rodar e confirmar verde (+ sem regressão nos routers)**

Run: `.venv/bin/python -m pytest tests/test_channel_join.py tests/test_onboarding_approver.py tests/test_bot_callback_data.py -v`
Expected: todos passed

- [ ] **Step 5: Commit**

```bash
git add webhook/bot/routers/channel_join.py webhook/bot/callback_data.py webhook/bot/keyboards.py webhook/bot/main.py tests/test_channel_join.py
git commit -m "feat(bot): join requests do canal com card de aprovação + allowed_updates no webhook"
```

---

### Task 8: Flood-wait retry no DM broadcast interno

**Files:**
- Modify: `webhook/bot/delivery.py` (loop de envio, linhas 29-37)
- Test: `tests/test_bot_delivery.py` (adicionar 1 teste)

**Interfaces:**
- Consumes/Produces: assinatura e retorno de `deliver_to_subscribers` inalterados.

- [ ] **Step 1: Escrever o teste que falha**

Adicionar ao final de `tests/test_bot_delivery.py`:

```python
@pytest.mark.asyncio
async def test_deliver_retries_on_flood_wait(fake_redis, mock_bot):
    from unittest.mock import MagicMock
    from aiogram.exceptions import TelegramRetryAfter
    _seed_users(fake_redis)
    flood = TelegramRetryAfter(method=MagicMock(), message="flood", retry_after=0)
    # user 100 floods once then succeeds on retry
    mock_bot.send_message = AsyncMock(side_effect=[flood, None])
    with patch("bot.delivery.get_bot", return_value=mock_bot):
        from bot.delivery import deliver_to_subscribers
        results = await deliver_to_subscribers("morning_check", "Test")
    assert results["sent"] == 1
    assert results["failed"] == 0
    assert mock_bot.send_message.await_count == 2
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `.venv/bin/python -m pytest tests/test_bot_delivery.py -v`
Expected: o teste novo FAIL (`sent == 0`, flood conta como falha hoje); os 3 antigos passam.

- [ ] **Step 3: Implementar**

Substituir `webhook/bot/delivery.py` inteiro por:

```python
"""Telegram delivery to subscribed users.

Sends workflow content to all approved users who have the matching
subscription enabled. Used by /store-draft when direct_delivery=true
(internal workflows only — client workflows go to the channel via
bot.channel_delivery).
"""

from __future__ import annotations

import asyncio
import logging

from aiogram.exceptions import TelegramRetryAfter

from bot.config import get_bot
from bot.users import get_subscribers_for_workflow

logger = logging.getLogger(__name__)


async def _send_with_flood_retry(bot, chat_id: int, message: str) -> None:
    """Send a DM; on flood-wait sleep retry_after and retry once."""
    try:
        await bot.send_message(chat_id, message)
    except TelegramRetryAfter as exc:
        logger.warning(f"flood-wait for {chat_id}: sleeping {exc.retry_after}s")
        await asyncio.sleep(exc.retry_after)
        await bot.send_message(chat_id, message)


async def deliver_to_subscribers(workflow_type: str, message: str) -> dict:
    """Send message to all subscribers of workflow_type.

    Returns {"sent": int, "failed": int, "errors": list[str]}
    """
    bot = get_bot()
    subscribers = get_subscribers_for_workflow(workflow_type)

    sent = 0
    failed = 0
    errors = []

    for user in subscribers:
        chat_id = user["chat_id"]
        try:
            await _send_with_flood_retry(bot, chat_id, message)
            sent += 1
        except Exception as exc:
            failed += 1
            errors.append(f"{chat_id}: {str(exc)[:100]}")
            logger.warning(f"Telegram delivery failed for {chat_id}: {exc}")

    logger.info(f"Telegram delivery [{workflow_type}]: {sent} sent, {failed} failed")
    return {"sent": sent, "failed": failed, "errors": errors}
```

- [ ] **Step 4: Rodar e confirmar verde**

Run: `.venv/bin/python -m pytest tests/test_bot_delivery.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add webhook/bot/delivery.py tests/test_bot_delivery.py
git commit -m "fix(bot): retry de flood-wait no DM broadcast interno"
```

---

### Task 9: Verificação final + status do spec

**Files:**
- Modify: `docs/superpowers/specs/2026-07-09-telegram-channel-delivery-design.md` (linha 4, status)

- [ ] **Step 1: Suite completa**

Run: `.venv/bin/python -m pytest`
Expected: tudo verde (ignorando `tests/archive`). Qualquer falha em teste pré-existente é regressão desta feature — investigar antes de seguir (suspeitos: default `telegram` da flag atingindo um caminho uazapi não gateado nos testes).

- [ ] **Step 2: Atualizar status do spec**

Na linha 4 do spec, trocar:

```markdown
- **Status:** Aprovado (design); aguardando revisão do spec para partir ao plano de implementação
```

por:

```markdown
- **Status:** Implementado (plano: docs/superpowers/plans/2026-07-09-telegram-channel-delivery.md); pendente rollout manual (§7)
```

- [ ] **Step 3: Commit final**

```bash
git add docs/superpowers/specs/2026-07-09-telegram-channel-delivery-design.md docs/superpowers/plans/2026-07-09-telegram-channel-delivery.md
git commit -m "docs: spec+plano da migração WhatsApp → canal Telegram"
```

- [ ] **Step 4: Checklist de rollout manual (fora do código — operador)**

Sequência do spec §7, executada pelo operador após deploy:

1. Criar o canal privado no Telegram; adicionar o bot como **admin** (permissões: postar, convidar, aprovar pedidos).
2. Descobrir o id do canal (encaminhar um post do canal pro @userinfobot, ou `getUpdates`) e setar `TELEGRAM_CLIENT_CHANNEL_ID` no Railway (serviço keen-stillness/web).
3. Conferir `CLIENT_DELIVERY_CHANNEL` ausente ou `=telegram` no Railway (default já é telegram).
4. Deploy; rodar `POST /admin/register-commands?chat_id=<admin>` para registrar `/convite`.
5. Testar com 1 relatório real (workflow de cliente) e observar o post no canal.
6. `/convite` → divulgar link/QR por e-mail/ligação aos ~74 clientes; aprovar join requests conforme chegam.
7. Rollback, se necessário: `CLIENT_DELIVERY_CHANNEL=uazapi` no Railway (volta o caminho WhatsApp sem redeploy de código).
