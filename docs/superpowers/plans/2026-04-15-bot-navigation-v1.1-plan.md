# Bot Navigation v1.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduzir ruído no chat (digest único no scrap em vez de 1 card/notícia), unificar rationale + curation no mesmo fluxo manual, e polir visualmente todas as mensagens de feedback (emoji, estrutura, rename "3 Agents" → "Writer", progresso play-by-play por agente).

**Architecture:** Router deixa de postar Telegram e deixa de rotear rationale auto; vira um stager puro com tag `type`. Scrap script (`platts_ingestion.py`) passa a enviar UM digest no final se houver novos items. Novos módulos pequenos: `webhook/digest.py` (formatter do digest) e `execution/core/agents_progress.py` (helper play-by-play). `query_handlers.py` e `app.py` ganham polish cosmético (emojis, separadores, ícones por tipo, Writer rename, confirmações mais estruturadas).

**Tech Stack:** Python 3.11 · Flask · redis-py · fakeredis · pytest

**Spec:** `docs/superpowers/specs/2026-04-15-bot-navigation-v1.1-design.md`

**Gotcha crítico do ambiente:** Path do projeto tem trailing space. Sempre: `cd "/Users/bigode/Dev/Antigravity WF "`.

**Gotcha Railway:** Dockerfile achata `webhook/` → `/app/`. Imports em `webhook/*.py` são bare (`import redis_queries`). `tests/conftest.py` já coloca `webhook/` no sys.path.

---

## Arquivos-alvo

### Criar
- `webhook/digest.py` — formatter do digest (format_ingestion_digest)
- `execution/core/agents_progress.py` — helper do play-by-play dos 3 agents
- `tests/test_digest.py` — 7 testes
- `tests/test_agents_progress.py` — 5 testes
- `tests/test_curation_router.py` — 4 testes (ou estende se já existir)

### Modificar
- `execution/curation/rationale_dispatcher.py` — TODO comment no topo
- `execution/curation/router.py` — `classify` anota type no item; remove post; `_stage_and_post` vira `_stage_only`; `route_items` retorna items + counters; sem rationale_processor
- `execution/scripts/platts_ingestion.py` — remove rationale_processor, chama `send_digest` ao final se novos > 0
- `execution/curation/telegram_poster.py` — card com título + ícone, meta em 1 linha, botões 2×2, botão "🖋️ Writer"
- `webhook/query_handlers.py` — format_queue_page (botões com título), format_stats (emojis + "No Writer"), format_history (ícones), format_rejections (🕒)
- `webhook/app.py` — renames cosméticos ("3 agents" → "Writer"), confirmações com 🕒 · 🆔 em 1 linha, progresso play-by-play via `agents_progress`
- `tests/test_query_handlers.py` — atualiza asserções dos 4 formatters (queue, stats, history, rejections)
- `tests/test_curation_telegram_poster.py` — atualiza card assertions

---

## Task 0: TODO no rationale_dispatcher (órfão após esta fase)

**Files:**
- Modify: `execution/curation/rationale_dispatcher.py` (docstring no topo)

- [ ] **Step 1: Read current top-of-file**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && head -15 execution/curation/rationale_dispatcher.py`
Expected: vê o docstring atual (se houver) + primeiros imports.

- [ ] **Step 2: Adicionar TODO no docstring**

Edit `execution/curation/rationale_dispatcher.py` — no topo do arquivo, substituir o primeiro docstring triple-quoted (ou adicionar se não tiver) pelo seguinte:

```python
"""Dispatches rationale items to the rationale AI pipeline.

TODO (v1.1+): Este módulo ficou ÓRFÃO após Bot Navigation v1.1 —
o router não o chama mais automaticamente (rationale agora passa
pela curadoria manual como qualquer notícia). Mantemos o código aqui
porque pode ser útil como utilitário chamado manualmente via script
ou como base pra uma fase futura de prompts dedicados de rationale.
Revisitar pra possível remoção quando essa decisão for tomada.
"""
```

- [ ] **Step 3: Sintaxe check**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python3 -c "import ast; ast.parse(open('execution/curation/rationale_dispatcher.py').read()); print('syntax ok')"`
Expected: `syntax ok`.

- [ ] **Step 4: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF "
git add execution/curation/rationale_dispatcher.py
git commit -m "docs(rationale): mark as orphan after Bot Navigation v1.1"
```

---

## Task 1: Router vira stager puro (sem post, sem rationale auto)

**Files:**
- Modify: `execution/curation/router.py`
- Create (or extend): `tests/test_curation_router.py`

- [ ] **Step 1: Check if test file exists**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && ls tests/test_curation_router.py 2>&1`

Se existir, append nos steps abaixo; senão, cria.

- [ ] **Step 2: Write failing tests**

Create (or append to) `tests/test_curation_router.py`:

```python
"""Tests for execution.curation.router (v1.1: stager puro)."""
import json
import pytest
import fakeredis


@pytest.fixture(autouse=True)
def _redis(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    from execution.curation import redis_client
    monkeypatch.setattr(redis_client, "_get_client", lambda: fake)
    monkeypatch.setattr(redis_client, "_client", None)
    yield fake


def test_classify_returns_rationale_for_rmw_rationale_tab():
    from execution.curation.router import classify
    item = {"source": "rmw", "tabName": "Rationale"}
    assert classify(item) == "rationale"


def test_classify_returns_rationale_for_rmw_lump_tab():
    from execution.curation.router import classify
    item = {"source": "rmw_market", "tabName": "Lump Premium"}
    assert classify(item) == "rationale"


def test_classify_returns_curation_default():
    from execution.curation.router import classify
    item = {"source": "platts", "tabName": "Iron Ore News"}
    assert classify(item) == "curation"


def test_route_items_stages_all_with_type_field(_redis):
    """Every item (curation OR rationale) lands in staging with a `type`."""
    from execution.curation.router import route_items
    items = [
        {"source": "platts", "title": "Iron Ore News 1", "tabName": "News"},
        {"source": "rmw", "title": "Daily Rationale", "tabName": "Rationale"},
    ]
    counters, staged = route_items(
        items=items, today_date="2026-04-15", today_br="15/04/2026",
        logger=None,
    )
    assert counters["total"] == 2
    assert counters["staged"] == 2
    assert counters["rationale_staged"] == 1
    assert counters["news_staged"] == 1
    assert counters["skipped_seen"] == 0
    assert len(staged) == 2
    types = {s["type"] for s in staged}
    assert types == {"news", "rationale"}
    # Cada item tem id preenchido
    assert all(s.get("id") for s in staged)


def test_route_items_respects_is_seen_dedup(_redis):
    from execution.curation.router import route_items
    from execution.curation import redis_client
    from execution.curation.id_gen import generate_id
    item = {"source": "platts", "title": "Duplicated", "tabName": "News"}
    item_id = generate_id("platts", "Duplicated")
    redis_client.mark_seen("2026-04-15", item_id)
    counters, staged = route_items(
        items=[item], today_date="2026-04-15", today_br="15/04/2026",
        logger=None,
    )
    assert counters["skipped_seen"] == 1
    assert counters["staged"] == 0
    assert staged == []


def test_route_items_does_not_call_telegram(_redis, monkeypatch):
    """Router must NOT post to Telegram — posting is caller's job now."""
    from execution.curation.router import route_items
    from execution.curation import telegram_poster
    def fail_if_called(*args, **kwargs):
        raise AssertionError("router should not call post_for_curation")
    monkeypatch.setattr(telegram_poster, "post_for_curation", fail_if_called)
    route_items(
        items=[{"source": "platts", "title": "X", "tabName": "News"}],
        today_date="2026-04-15", today_br="15/04/2026", logger=None,
    )
```

- [ ] **Step 3: Run tests — expect fails**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_curation_router.py -v`
Expected: FAIL em `test_route_items_stages_all_with_type_field` com TypeError (assinatura antiga exige `chat_id`, `preview_base_url`, `rationale_processor`).

- [ ] **Step 4: Refactor router.py**

Replace `execution/curation/router.py` with:

```python
"""Classify dataset items and stage them in Redis with a `type` tag.

Post-v1.1: This module no longer posts to Telegram nor dispatches
rationale automatically. It stages everything in `platts:staging:*`
with a `type` field (`"news"` | `"rationale"`) so the caller can
send a single ingestion digest and the operator can review each item
manually via /queue.
"""
import re
from typing import List, Optional, Tuple

from execution.core.logger import WorkflowLogger
from execution.curation import redis_client
from execution.curation.id_gen import generate_id

_RATIONALE_TAB_RE = re.compile(r"\b(Rationale|Lump)\b", re.IGNORECASE)


def classify(item: dict) -> str:
    """Return 'rationale' for RMW Rationale/Lump items, 'curation' otherwise."""
    source = item.get("source") or ""
    tab_name = item.get("tabName") or ""
    if source.startswith("rmw") and _RATIONALE_TAB_RE.search(tab_name):
        return "rationale"
    return "curation"


def _type_tag(item: dict) -> str:
    """Return the staging type tag: 'rationale' → 'rationale', else 'news'."""
    return "rationale" if classify(item) == "rationale" else "news"


def _stage_only(item: dict, item_id: str, item_type: str, today_date: str) -> dict:
    """Stage one item in Redis and mark seen. Returns the dict that was staged."""
    to_stage = {**item, "id": item_id, "type": item_type}
    redis_client.set_staging(item_id, to_stage)
    redis_client.mark_seen(today_date, item_id)
    return to_stage


def route_items(
    items: List[dict],
    today_date: str,
    today_br: str,
    logger: Optional[WorkflowLogger] = None,
) -> Tuple[dict, List[dict]]:
    """Classify + stage every dataset item. Returns (counters, staged_items).

    counters keys: total, staged, news_staged, rationale_staged, skipped_seen.
    staged_items: list of dicts actually written to Redis (newest-first NOT
    guaranteed here — sort in the caller if needed).
    """
    log = logger or WorkflowLogger("CurationRouter")
    counters = {
        "total": len(items),
        "staged": 0,
        "news_staged": 0,
        "rationale_staged": 0,
        "skipped_seen": 0,
    }
    staged: List[dict] = []

    for item in items:
        item_type = _type_tag(item)
        item_id = generate_id(item.get("source", ""), item.get("title", ""))
        if redis_client.is_seen(today_date, item_id):
            counters["skipped_seen"] += 1
            continue
        staged_item = _stage_only(item, item_id, item_type, today_date)
        staged.append(staged_item)
        counters["staged"] += 1
        if item_type == "rationale":
            counters["rationale_staged"] += 1
        else:
            counters["news_staged"] += 1

    log.info(f"Staged {counters['staged']} items "
             f"({counters['news_staged']} news, {counters['rationale_staged']} rationale); "
             f"{counters['skipped_seen']} skipped as seen")
    return counters, staged
```

- [ ] **Step 5: Run tests — expect pass**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_curation_router.py -v`
Expected: 6 passing.

- [ ] **Step 6: Commit**

```bash
git add execution/curation/router.py tests/test_curation_router.py
git commit -m "refactor(router): stage only, tag type, drop auto-rationale and Telegram post

Router becomes a pure stager. Classification is preserved as a type tag
on each staged dict. Rationale items no longer trigger rationale_dispatcher;
they land in platts:staging:* like any other item and go through manual
curation via /queue. Caller is responsible for post-scrap notification
(the digest) — router returns (counters, staged_items) so the caller
has what it needs."
```

---

## Task 2: digest formatter module

**Files:**
- Create: `webhook/digest.py`
- Create: `tests/test_digest.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_digest.py`:

```python
"""Tests for webhook.digest (ingestion digest formatter)."""


def test_digest_returns_none_on_zero_staged():
    from digest import format_ingestion_digest
    counters = {"staged": 0, "news_staged": 0, "rationale_staged": 0}
    result = format_ingestion_digest(counters, [])
    assert result is None


def test_digest_news_only_hides_rationale_line():
    from digest import format_ingestion_digest
    counters = {"staged": 3, "news_staged": 3, "rationale_staged": 0}
    items = [
        {"title": "Alpha", "type": "news"},
        {"title": "Beta", "type": "news"},
        {"title": "Gamma", "type": "news"},
    ]
    text, markup = format_ingestion_digest(counters, items)
    assert "Ingestão · 3 novas" in text
    assert "🗞️ 3 notícias" in text
    assert "rationale" not in text.lower()
    assert "🗞️ Alpha" in text
    assert "🗞️ Beta" in text


def test_digest_rationale_only_hides_news_line():
    from digest import format_ingestion_digest
    counters = {"staged": 2, "news_staged": 0, "rationale_staged": 2}
    items = [
        {"title": "Daily Rationale", "type": "rationale"},
        {"title": "Lump Premium", "type": "rationale"},
    ]
    text, _ = format_ingestion_digest(counters, items)
    assert "📊 2 rationale" in text
    assert "notícias" not in text
    assert "📊 Daily Rationale" in text


def test_digest_mixed_shows_tree():
    from digest import format_ingestion_digest
    counters = {"staged": 5, "news_staged": 3, "rationale_staged": 2}
    items = [{"title": f"Item {i}", "type": "news"} for i in range(5)]
    text, _ = format_ingestion_digest(counters, items)
    assert "├ 🗞️ 3 notícias" in text
    assert "└ 📊 2 rationale" in text


def test_digest_preview_limits_to_3_items():
    from digest import format_ingestion_digest
    counters = {"staged": 5, "news_staged": 5, "rationale_staged": 0}
    items = [{"title": f"Title {i}", "type": "news"} for i in range(5)]
    text, _ = format_ingestion_digest(counters, items)
    assert "Title 0" in text
    assert "Title 1" in text
    assert "Title 2" in text
    assert "Title 3" not in text
    assert "+2 mais" in text


def test_digest_no_plus_when_exactly_3():
    from digest import format_ingestion_digest
    counters = {"staged": 3, "news_staged": 3, "rationale_staged": 0}
    items = [{"title": f"T{i}", "type": "news"} for i in range(3)]
    text, _ = format_ingestion_digest(counters, items)
    assert "+0 mais" not in text
    assert "mais" not in text


def test_digest_escapes_markdown_in_titles():
    from digest import format_ingestion_digest
    counters = {"staged": 1, "news_staged": 1, "rationale_staged": 0}
    items = [{"title": "Vale_Q2 *report* [draft]", "type": "news"}]
    text, _ = format_ingestion_digest(counters, items)
    assert "*report*" not in text
    assert "Vale_Q2" not in text
    assert "\\*report\\*" in text or "report" in text.replace("\\*", "")


def test_digest_markup_has_open_queue_button():
    from digest import format_ingestion_digest
    counters = {"staged": 1, "news_staged": 1, "rationale_staged": 0}
    items = [{"title": "X", "type": "news"}]
    _, markup = format_ingestion_digest(counters, items)
    assert markup is not None
    buttons = markup["inline_keyboard"]
    assert len(buttons) == 1
    assert buttons[0][0]["callback_data"] == "queue_page:1"
    assert "🔍" in buttons[0][0]["text"]
    assert "fila" in buttons[0][0]["text"].lower()
```

- [ ] **Step 2: Run tests — expect ImportError**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_digest.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'digest'`.

- [ ] **Step 3: Implement digest module**

Create `webhook/digest.py`:

```python
"""Formatter for the post-scrap ingestion digest.

Called by the Platts ingestion script after staging is complete. Returns
None if zero new items (caller sends nothing). Otherwise returns
(text, reply_markup) ready for send_telegram_message.
"""
from typing import Optional, Tuple

from execution.curation.telegram_poster import _escape_md

_PREVIEW_LIMIT = 3
_TITLE_TRUNCATE = 60
_ICON_BY_TYPE = {"news": "🗞️", "rationale": "📊"}


def _truncate(text: str, limit: int = _TITLE_TRUNCATE) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def format_ingestion_digest(
    counters: dict,
    staged_items: list,
) -> Optional[Tuple[str, dict]]:
    """Build the digest (text, markup) or None if 0 staged.

    counters: dict with keys staged, news_staged, rationale_staged.
    staged_items: list of dicts with keys title, type (already tagged
                  by router).
    """
    total = counters.get("staged", 0)
    if total == 0:
        return None

    news = counters.get("news_staged", 0)
    rationale = counters.get("rationale_staged", 0)

    lines = [f"📥 *Ingestão · {total} novas*"]
    # Tree showing non-zero branches only
    if news and rationale:
        lines.append(f"├ 🗞️ {news} notícias")
        lines.append(f"└ 📊 {rationale} rationale")
    elif news:
        lines.append(f"└ 🗞️ {news} notícias")
    elif rationale:
        lines.append(f"└ 📊 {rationale} rationale")

    # Preview: first _PREVIEW_LIMIT items
    preview = staged_items[:_PREVIEW_LIMIT]
    if preview:
        lines.append("")
        for item in preview:
            icon = _ICON_BY_TYPE.get(item.get("type", "news"), "🗞️")
            title = _escape_md(_truncate(item.get("title") or ""))
            lines.append(f"• {icon} {title}")
        remaining = total - len(preview)
        if remaining > 0:
            lines.append(f"+{remaining} mais")

    text = "\n".join(lines)
    markup = {
        "inline_keyboard": [[
            {"text": "🔍 Abrir fila", "callback_data": "queue_page:1"},
        ]],
    }
    return text, markup
```

- [ ] **Step 4: Run tests — expect pass**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_digest.py -v`
Expected: 8 passing.

- [ ] **Step 5: Commit**

```bash
git add webhook/digest.py tests/test_digest.py
git commit -m "feat(digest): ingestion digest formatter with tree + preview + button"
```

---

## Task 3: platts_ingestion chama digest no final

**Files:**
- Modify: `execution/scripts/platts_ingestion.py`

- [ ] **Step 1: Locate the caller block**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && grep -n "rationale_processor\|route_items\|record_success" execution/scripts/platts_ingestion.py`

Esperado mostrar o bloco entre linhas ~150-165 onde `route_items` é chamado hoje com `rationale_processor`.

- [ ] **Step 2: Remove rationale_processor + dispatch do digest**

Replace o bloco de chamada do `route_items` em `execution/scripts/platts_ingestion.py`. O bloco atual é:

```python
        def rationale_processor(rationale_items, today_br_inner):
            return rationale_dispatcher.process(rationale_items, today_br_inner, logger=logger)

        counters = router.route_items(
            items=articles,
            today_date=date_iso,
            today_br=today_br,
            chat_id=chat_id,
            preview_base_url=preview_base_url,
            rationale_processor=rationale_processor,
            logger=logger,
        )
        logger.info(f"Route summary: {counters}")
        state_store.record_success(WORKFLOW_NAME, counters, 0)
```

Substituir por:

```python
        counters, staged = router.route_items(
            items=articles,
            today_date=date_iso,
            today_br=today_br,
            logger=logger,
        )
        logger.info(f"Route summary: {counters}")

        # v1.1: send single ingestion digest if any new items were staged
        if counters.get("staged", 0) > 0:
            try:
                from webhook.digest import format_ingestion_digest
                from execution.integrations.telegram_client import TelegramClient
                digest_out = format_ingestion_digest(counters, staged)
                if digest_out is not None:
                    text, markup = digest_out
                    TelegramClient().send_message(
                        chat_id=chat_id, text=text, reply_markup=markup,
                    )
                    logger.info(f"Digest sent to chat {chat_id}")
            except Exception as exc:
                logger.warning(f"Digest send failed: {exc}")

        state_store.record_success(WORKFLOW_NAME, counters, 0)
```

Remover também o import agora não usado no topo do arquivo (linha 20):
```python
from execution.curation import rationale_dispatcher, router
```
vira:
```python
from execution.curation import router
```

- [ ] **Step 3: Verify TelegramClient signature matches**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && grep -n "def send_message\|class TelegramClient" execution/integrations/telegram_client.py | head -5`

Esperado mostrar signature: `def send_message(self, chat_id, text, reply_markup=None, ...)`. Se o parâmetro for chamado diferente (ex: `markup` em vez de `reply_markup`), ajuste a chamada acima.

- [ ] **Step 4: Dry-run check**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python3 -c "import ast; ast.parse(open('execution/scripts/platts_ingestion.py').read()); print('syntax ok')"`
Expected: `syntax ok`.

- [ ] **Step 5: Full suite (checa regressão)**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest -q`
Expected: todos os testes ainda passam.

- [ ] **Step 6: Commit**

```bash
git add execution/scripts/platts_ingestion.py
git commit -m "feat(ingestion): send single digest after staging (v1.1)

Replaces the per-item curation card spam with one ingestion digest
message at the end of each scrap run. Uses router's new (counters,
staged) return shape — rationale_processor is gone since rationale
now goes through the same manual curation flow."
```

---

## Task 4: agents_progress helper

**Files:**
- Create: `execution/core/agents_progress.py`
- Create: `tests/test_agents_progress.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_agents_progress.py`:

```python
"""Tests for execution.core.agents_progress."""


def test_writer_phase_active():
    from execution.core.agents_progress import format_pipeline_progress
    text = format_pipeline_progress(current="Writer")
    assert "🖋️ *Writer* escrevendo... (1/3)" in text
    assert "⏳ Writer" in text
    assert "⏳ Reviewer" in text
    assert "⏳ Finalizer" in text


def test_reviewer_phase_active():
    from execution.core.agents_progress import format_pipeline_progress
    text = format_pipeline_progress(current="Reviewer", done=["Writer"])
    assert "🔍 *Reviewer* analisando... (2/3)" in text
    assert "✅ Writer" in text
    assert "⏳ Reviewer" in text
    assert "⏳ Finalizer" in text


def test_finalizer_phase_active():
    from execution.core.agents_progress import format_pipeline_progress
    text = format_pipeline_progress(current="Finalizer", done=["Writer", "Reviewer"])
    assert "✨ *Finalizer* polindo... (3/3)" in text
    assert "✅ Writer" in text
    assert "✅ Reviewer" in text
    assert "⏳ Finalizer" in text


def test_all_done():
    from execution.core.agents_progress import format_pipeline_progress
    text = format_pipeline_progress(current=None, done=["Writer", "Reviewer", "Finalizer"])
    assert "✅ *Draft pronto*" in text
    assert "✅ Writer" in text
    assert "✅ Reviewer" in text
    assert "✅ Finalizer" in text


def test_error_in_reviewer():
    from execution.core.agents_progress import format_pipeline_progress
    text = format_pipeline_progress(current="Reviewer", done=["Writer"], error="timeout após 60s")
    assert "❌ Erro em *Reviewer*" in text
    assert "✅ Writer" in text
    assert "❌ Reviewer" in text
    assert "⏸ Finalizer" in text
    assert "timeout após 60s" in text
```

- [ ] **Step 2: Run tests — expect fail**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_agents_progress.py -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement helper**

Create `execution/core/agents_progress.py`:

```python
"""Format a 3-phase pipeline progress display (edited in-place in Telegram).

Phases are Writer → Reviewer → Finalizer. Each call returns the full
message body the caller then passes to edit_message.
"""
from typing import Iterable, Optional

_PHASES = ("Writer", "Reviewer", "Finalizer")
_PHASE_HEADER = {
    "Writer":    "🖋️ *Writer* escrevendo... (1/3)",
    "Reviewer":  "🔍 *Reviewer* analisando... (2/3)",
    "Finalizer": "✨ *Finalizer* polindo... (3/3)",
}
_SEPARATOR = "────────────────────"


def format_pipeline_progress(
    current: Optional[str],
    done: Optional[Iterable[str]] = None,
    error: Optional[str] = None,
) -> str:
    """Build the progress text for a given pipeline state.

    current: the phase currently running (None if all done).
    done: iterable of phases already completed successfully.
    error: if set, marks `current` as failed and later phases as paused.
    """
    done_set = set(done or ())

    # Header line
    if error and current:
        header = f"❌ Erro em *{current}*"
    elif current is None:
        header = "✅ *Draft pronto*"
    else:
        header = _PHASE_HEADER.get(current, f"⏳ *{current}*...")

    # Per-phase status
    lines = [header, _SEPARATOR]
    reached_error = False
    for phase in _PHASES:
        if phase in done_set:
            icon = "✅"
        elif error and phase == current:
            icon = "❌"
            reached_error = True
        elif reached_error:
            icon = "⏸"
        elif current == phase:
            icon = "⏳"
        else:
            icon = "⏳"
        lines.append(f"{icon} {phase}")

    if error:
        lines.append("")
        lines.append(f"_{error}_")

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests — expect pass**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_agents_progress.py -v`
Expected: 5 passing.

- [ ] **Step 5: Commit**

```bash
git add execution/core/agents_progress.py tests/test_agents_progress.py
git commit -m "feat(progress): 3-phase pipeline progress formatter"
```

---

## Task 5: /queue com títulos como botões (substitui "N. Abrir")

**Files:**
- Modify: `webhook/query_handlers.py` (função `format_queue_page`)
- Modify: `tests/test_query_handlers.py` (asserções do /queue)

- [ ] **Step 1: Atualizar os testes**

No arquivo `tests/test_query_handlers.py`, substituir o bloco dos 5 testes do `/queue` existentes pelo seguinte (cobrindo o novo formato de botão título + ícone por tipo):

```python
def test_queue_empty(fake_redis):
    from webhook.query_handlers import format_queue_page
    text, markup = format_queue_page(page=1)
    assert text == "*🗂️ STAGING*\n\nNenhum item aguardando."
    assert markup is None


def test_queue_single_page_titles_in_buttons(fake_redis):
    from webhook.query_handlers import format_queue_page
    for i, ts in enumerate(["10:00", "09:00", "08:00"]):
        fake_redis.set(f"platts:staging:item{i}", json.dumps({
            "id": f"item{i}", "title": f"Title {i}", "type": "news",
            "stagedAt": f"2026-04-15T{ts}:00Z"
        }))
    text, markup = format_queue_page(page=1)
    assert "*🗂️ STAGING · 3 items*" in text
    # Texto NÃO deve mais conter "1. Title 0"/"N. Abrir"
    assert "1. Title" not in text
    assert "Abrir" not in text
    # Mas os 3 botões (1 por item) carregam o título com ícone
    buttons = markup["inline_keyboard"]
    assert len(buttons) == 3
    assert buttons[0][0]["text"] == "🗞️ Title 0"
    assert buttons[0][0]["callback_data"] == "queue_open:item0"


def test_queue_button_uses_rationale_icon(fake_redis):
    from webhook.query_handlers import format_queue_page
    fake_redis.set("platts:staging:x", json.dumps({
        "id": "x", "title": "Daily Rationale", "type": "rationale",
        "stagedAt": "2026-04-15T10:00:00Z"
    }))
    _, markup = format_queue_page(page=1)
    assert markup["inline_keyboard"][0][0]["text"] == "📊 Daily Rationale"


def test_queue_paginated(fake_redis):
    from webhook.query_handlers import format_queue_page
    for i in range(12):
        fake_redis.set(f"platts:staging:i{i:02d}", json.dumps({
            "id": f"i{i:02d}", "title": f"Title {i:02d}", "type": "news",
            "stagedAt": f"2026-04-15T{i:02d}:00:00Z"
        }))
    text_p1, markup_p1 = format_queue_page(page=1)
    assert "*🗂️ STAGING · 12 items*" in text_p1
    # 5 item rows + 1 pagination row
    assert len(markup_p1["inline_keyboard"]) == 6
    # Item buttons têm o título
    assert markup_p1["inline_keyboard"][0][0]["text"] == "🗞️ Title 11"
    # Pagination row (última)
    pag_texts = [b["text"] for b in markup_p1["inline_keyboard"][-1]]
    assert any("1/3" in t for t in pag_texts)
    assert any("próximo" in t.lower() for t in pag_texts)
    assert not any("anterior" in t.lower() for t in pag_texts)


def test_queue_truncates_long_title_in_button(fake_redis):
    from webhook.query_handlers import format_queue_page
    long_title = "B" * 80
    fake_redis.set("platts:staging:x", json.dumps({
        "id": "x", "title": long_title, "type": "news",
        "stagedAt": "2026-04-15T10:00:00Z"
    }))
    _, markup = format_queue_page(page=1)
    btn_text = markup["inline_keyboard"][0][0]["text"]
    # Título truncado em 40 chars + "…" + ícone + espaço ≤ 64
    assert btn_text.startswith("🗞️ ")
    assert "…" in btn_text
    assert len(btn_text) <= 64


def test_queue_escapes_markdown_in_button_text(fake_redis):
    """Button text is plain (NOT markdown-parsed by Telegram) — but we still
    want *markers* removed so the operator sees clean text."""
    from webhook.query_handlers import format_queue_page
    fake_redis.set("platts:staging:x", json.dumps({
        "id": "x", "title": "Vale *Q2* [report]", "type": "news",
        "stagedAt": "2026-04-15T10:00:00Z"
    }))
    _, markup = format_queue_page(page=1)
    btn_text = markup["inline_keyboard"][0][0]["text"]
    # Button text is plain, so markdown chars are OK literally
    assert "Vale" in btn_text
```

- [ ] **Step 2: Run — tests fail**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_query_handlers.py -k queue -v`
Expected: falhas em todos os testes do queue.

- [ ] **Step 3: Implementar o novo `format_queue_page`**

Em `webhook/query_handlers.py`, substituir o `format_queue_page` existente pelo seguinte:

```python
_QUEUE_PAGE_SIZE = 5
_QUEUE_BTN_TITLE_MAX = 40
_ICON_BY_TYPE = {"news": "🗞️", "rationale": "📊"}


def _type_icon(item: dict) -> str:
    return _ICON_BY_TYPE.get(item.get("type", "news"), "🗞️")


def _queue_button_text(item: dict) -> str:
    icon = _type_icon(item)
    title = (item.get("title") or "").strip()
    if len(title) > _QUEUE_BTN_TITLE_MAX:
        title = title[:_QUEUE_BTN_TITLE_MAX] + "…"
    return f"{icon} {title}"


def format_queue_page(page: int = 1) -> tuple[str, Optional[dict]]:
    """Return (text, reply_markup) for /queue at given 1-indexed page.

    reply_markup is None when there are no items. Each item row has a
    single callback button `queue_open:<id>` whose *text* is the item
    title prefixed by a type icon (🗞️ / 📊). Pagination row appended
    if total_pages > 1.
    """
    items = redis_queries.list_staging(limit=200)
    total = len(items)
    if total == 0:
        return "*🗂️ STAGING*\n\nNenhum item aguardando.", None

    total_pages = (total + _QUEUE_PAGE_SIZE - 1) // _QUEUE_PAGE_SIZE
    page = max(1, min(page, total_pages))
    start = (page - 1) * _QUEUE_PAGE_SIZE
    end = start + _QUEUE_PAGE_SIZE
    page_items = items[start:end]

    text = f"*🗂️ STAGING · {total} items*"

    keyboard: list[list[dict]] = []
    for item in page_items:
        item_id = item.get("id") or ""
        keyboard.append([{
            "text": _queue_button_text(item),
            "callback_data": f"queue_open:{item_id}",
        }])

    if total_pages > 1:
        row: list[dict] = []
        if page > 1:
            row.append({"text": "⬅ anterior", "callback_data": f"queue_page:{page - 1}"})
        row.append({"text": f"{page}/{total_pages}", "callback_data": "noop"})
        if page < total_pages:
            row.append({"text": "próximo ➡", "callback_data": f"queue_page:{page + 1}"})
        keyboard.append(row)

    return text, {"inline_keyboard": keyboard}
```

- [ ] **Step 4: Run — tests pass**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_query_handlers.py -k queue -v`
Expected: 6 passing.

- [ ] **Step 5: Full suite**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest -q`
Expected: todos ainda passam.

- [ ] **Step 6: Commit**

```bash
git add webhook/query_handlers.py tests/test_query_handlers.py
git commit -m "feat(queue): button text is title with type icon (replaces N. Abrir)"
```

---

## Task 6: /stats com emojis + "No Writer"

**Files:**
- Modify: `webhook/query_handlers.py` (função `format_stats`)
- Modify: `tests/test_query_handlers.py` (asserções do /stats)

- [ ] **Step 1: Atualizar os testes**

Em `tests/test_query_handlers.py`, substituir os 2 testes do `/stats` pelo seguinte:

```python
def test_stats_empty_day(fake_redis):
    from webhook.query_handlers import format_stats
    text = format_stats("2026-04-15")
    assert "*📊 HOJE · 15/abr*" in text
    assert "────" in text
    assert "🔎 Scraped" in text
    assert "🗂️ Staging" in text
    assert "📦 Arquivados" in text
    assert "❌ Recusados" in text
    assert "🖋️ No Writer" in text
    # Legacy label must be gone
    assert "Pipeline" not in text


def test_stats_populated(fake_redis):
    from webhook.query_handlers import format_stats
    fake_redis.sadd("platts:seen:2026-04-15", "a", "b", "c", "d")
    fake_redis.set("platts:staging:s1", json.dumps({"id": "s1"}))
    fake_redis.set("platts:archive:2026-04-15:x1", json.dumps({"id": "x1"}))
    fake_redis.set("platts:archive:2026-04-15:x2", json.dumps({"id": "x2"}))
    fake_redis.sadd("platts:pipeline:processed:2026-04-15", "p1")
    text = format_stats("2026-04-15")
    assert "🔎 Scraped" in text and "4" in text
    assert "🗂️ Staging" in text and "1" in text
    assert "📦 Arquivados" in text and "2" in text
    assert "🖋️ No Writer" in text and "1" in text
```

- [ ] **Step 2: Run — tests fail**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_query_handlers.py -k stats -v`
Expected: failures.

- [ ] **Step 3: Implementar o novo `format_stats`**

Substituir em `webhook/query_handlers.py`:

```python
def format_stats(date_iso: str) -> str:
    """Return /stats text for the given ISO date (polished layout)."""
    stats = redis_queries.stats_for_date(date_iso)
    short = _format_short_date(date_iso) or date_iso
    lines = [
        f"*📊 HOJE · {short}*",
        "────────────────────",
        f"🔎 Scraped        {stats['scraped']}",
        f"🗂️ Staging        {stats['staging']}",
        f"📦 Arquivados     {stats['archived']}",
        f"❌ Recusados       {stats['rejected']}",
        f"🖋️ No Writer       {stats['pipeline']}",
    ]
    return "\n".join(lines)
```

Keyspace `platts:pipeline:processed:<date>` não muda (só o label visível).

- [ ] **Step 4: Run — tests pass**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_query_handlers.py -k stats -v`
Expected: 2 passing.

- [ ] **Step 5: Commit**

```bash
git add webhook/query_handlers.py tests/test_query_handlers.py
git commit -m "feat(stats): emoji per line + separator, rename Pipeline→No Writer"
```

---

## Task 7: /history com ícones por tipo + separador

**Files:**
- Modify: `webhook/query_handlers.py` (função `format_history`)
- Modify: `tests/test_query_handlers.py` (asserções do /history)

- [ ] **Step 1: Atualizar os testes**

Em `tests/test_query_handlers.py`, substituir os 3 testes do `/history` pelo seguinte:

```python
def test_history_empty(fake_redis):
    from webhook.query_handlers import format_history
    text = format_history()
    assert text == "*📚 ARQUIVADOS*\n\nNenhum item arquivado."


def test_history_formats_items_with_type_icon(fake_redis):
    from webhook.query_handlers import format_history
    fake_redis.set("platts:archive:2026-04-14:a", json.dumps({
        "id": "a", "title": "Bonds Municipais", "type": "news",
        "archivedAt": "2026-04-14T10:00:00+00:00"
    }))
    fake_redis.set("platts:archive:2026-04-13:b", json.dumps({
        "id": "b", "title": "Daily Rationale", "type": "rationale",
        "archivedAt": "2026-04-13T08:00:00+00:00"
    }))
    text = format_history()
    assert "*📚 ARQUIVADOS · 2 mais recentes*" in text
    assert "────" in text
    assert "1. 🗞️ Bonds Municipais — 14/abr" in text
    assert "2. 📊 Daily Rationale — 13/abr" in text


def test_history_falls_back_to_news_icon_when_type_missing(fake_redis):
    """Legacy archived items (pre-v1.1) don't carry `type`; default to news icon."""
    from webhook.query_handlers import format_history
    fake_redis.set("platts:archive:2026-04-14:legacy", json.dumps({
        "id": "legacy", "title": "Legacy",
        "archivedAt": "2026-04-14T10:00:00+00:00"
    }))
    text = format_history()
    assert "1. 🗞️ Legacy — 14/abr" in text


def test_history_truncates_long_title(fake_redis):
    from webhook.query_handlers import format_history
    long_title = "A" * 80
    fake_redis.set("platts:archive:2026-04-15:x", json.dumps({
        "id": "x", "title": long_title, "type": "news",
        "archivedAt": "2026-04-15T10:00:00+00:00"
    }))
    text = format_history()
    assert "A" * 60 + "…" in text
    assert "A" * 61 not in text


def test_history_escapes_markdown_in_title(fake_redis):
    from webhook.query_handlers import format_history, _escape_md
    fake_redis.set("platts:archive:2026-04-15:x", json.dumps({
        "id": "x", "title": "Vale_Q2 *bonds*", "type": "news",
        "archivedAt": "2026-04-15T10:00:00+00:00",
    }))
    text = format_history()
    assert "*bonds*" not in text
    assert "Vale_Q2" not in text
    assert _escape_md("Vale_Q2 *bonds*") in text
```

- [ ] **Step 2: Run — tests fail**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_query_handlers.py -k history -v`
Expected: failures.

- [ ] **Step 3: Implementar**

Substituir `format_history` em `webhook/query_handlers.py`:

```python
def format_history(limit: int = 10) -> str:
    """Return /history text — last N archived items cross-date, with icons."""
    items = redis_queries.list_archive_recent(limit=limit)
    if not items:
        return "*📚 ARQUIVADOS*\n\nNenhum item arquivado."
    lines = [
        f"*📚 ARQUIVADOS · {len(items)} mais recentes*",
        "────────────────────",
    ]
    for i, item in enumerate(items, start=1):
        icon = _type_icon(item)
        title = _escape_md(_truncate(item.get("title") or ""))
        date = _format_short_date(item.get("archived_date") or "")
        lines.append(f"{i}. {icon} {title} — {date}")
    return "\n".join(lines)
```

`_type_icon` já existe do Task 5 no mesmo arquivo.

- [ ] **Step 4: Run — tests pass**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_query_handlers.py -k history -v`
Expected: 5 passing.

- [ ] **Step 5: Commit**

```bash
git add webhook/query_handlers.py tests/test_query_handlers.py
git commit -m "feat(history): type icon per item + separator"
```

---

## Task 8: /rejections polish (🕒 prefix + separador)

**Files:**
- Modify: `webhook/query_handlers.py` (função `format_rejections`)
- Modify: `tests/test_query_handlers.py`

- [ ] **Step 1: Atualizar os testes**

Em `tests/test_query_handlers.py`, substituir os 4 testes do `/rejections`:

```python
def test_rejections_empty(fake_redis):
    from webhook.query_handlers import format_rejections
    text = format_rejections()
    assert text == "*💭 RECUSAS*\n\nNenhuma recusa registrada."


def test_rejections_with_and_without_reason(fake_redis):
    from webhook.query_handlers import format_rejections
    from webhook.redis_queries import save_feedback
    save_feedback("curate_reject", "a", 1, "", "First")
    time.sleep(0.01)
    save_feedback("curate_reject", "b", 1, "duplicata", "Second")
    text = format_rejections()
    assert "*💭 RECUSAS · últimas 2*" in text
    assert "────" in text
    assert "🕒" in text
    assert '"duplicata"' in text
    assert "_(sem razão)_" in text


def test_rejections_truncates_long_reason(fake_redis):
    from webhook.query_handlers import format_rejections
    from webhook.redis_queries import save_feedback
    long = "x" * 120
    save_feedback("curate_reject", "a", 1, long, "T")
    text = format_rejections()
    assert "x" * 80 + "…" in text
    assert "x" * 81 not in text


def test_rejections_escapes_markdown_in_reason(fake_redis):
    from webhook.query_handlers import format_rejections, _escape_md
    from webhook.redis_queries import save_feedback
    save_feedback("curate_reject", "a", 1, "dup of *foo* [bar]", "T")
    text = format_rejections()
    assert "*foo*" not in text
    assert "[bar]" not in text
    assert _escape_md("dup of *foo* [bar]") in text
```

- [ ] **Step 2: Run — tests fail**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_query_handlers.py -k rejections -v`
Expected: failures.

- [ ] **Step 3: Implementar**

Substituir `format_rejections` em `webhook/query_handlers.py`:

```python
def format_rejections(limit: int = 10) -> str:
    """Return /rejections text — last N feedback entries with time + reason."""
    entries = redis_queries.list_feedback(limit=limit)
    if not entries:
        return "*💭 RECUSAS*\n\nNenhuma recusa registrada."
    lines = [
        f"*💭 RECUSAS · últimas {len(entries)}*",
        "────────────────────",
    ]
    for i, entry in enumerate(entries, start=1):
        when = _format_hhmm(entry.get("timestamp") or 0)
        reason = entry.get("reason") or ""
        if reason:
            reason_fmt = f'"{_escape_md(_truncate(reason, 80))}"'
        else:
            reason_fmt = "_(sem razão)_"
        lines.append(f"{i}. 🕒 {when} · {reason_fmt}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run — tests pass**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_query_handlers.py -k rejections -v`
Expected: 4 passing.

- [ ] **Step 5: Commit**

```bash
git add webhook/query_handlers.py tests/test_query_handlers.py
git commit -m "feat(rejections): emoji header + separator + 🕒 prefix per entry"
```

---

## Task 9: Card de curadoria polido + botão Writer

**Files:**
- Modify: `execution/curation/telegram_poster.py`
- Modify: `tests/test_curation_telegram_poster.py`

- [ ] **Step 1: Inspecionar testes atuais**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && grep -n "def test_\|🤖 3 Agents\|post_for_curation" tests/test_curation_telegram_poster.py | head -20`

Entender quais assertions hoje olham pra "🤖 3 Agents" (a que vamos renomear) e pro layout antigo (meta em várias linhas).

- [ ] **Step 2: Ler a implementação atual**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && sed -n '85,160p' execution/curation/telegram_poster.py`

- [ ] **Step 3: Atualizar testes**

Editar `tests/test_curation_telegram_poster.py`: em cada teste que hoje asserta strings do card, trocar:

- `"🤖 3 Agents"` → `"🖋️ Writer"`
- Se algum teste asserta layout de meta em múltiplas linhas (ex: `"📰 Platts"` em linha isolada), ajustar pra esperar a nova linha única:
  `"📅 ... · 📰 Platts · 🔖 ..."` (separador `·`).
- Se algum teste verifica título do card, agora ele vem como `*🗞️ Título*` (ou `*📊 Título*` para rationale) — a primeira linha de bold com o ícone de tipo.
- Acrescentar um teste novo pro ícone rationale:

```python
def test_post_for_curation_uses_rationale_icon_when_type_rationale(mock_client):
    from execution.curation.telegram_poster import post_for_curation
    item = {
        "id": "r1", "type": "rationale", "title": "Daily Rationale",
        "fullText": "preview", "publishDate": "04/15/2026", "source": "rmw",
        "tabName": "Rationale",
    }
    post_for_curation(chat_id=111, item=item, preview_base_url="https://example.com")
    sent = mock_client.send_message.call_args
    text = sent.kwargs.get("text") or sent.args[1]
    assert text.startswith("*📊 Daily Rationale*")
```

- [ ] **Step 4: Run tests — expect failures**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_curation_telegram_poster.py -v`
Expected: falhas nos testes que batem no layout antigo.

- [ ] **Step 5: Rewrite `post_for_curation` body**

Em `execution/curation/telegram_poster.py`, reescrever a função `post_for_curation` pra este formato:

```python
def post_for_curation(chat_id: int, item: dict, preview_base_url: str) -> None:
    """Send one curation card to the operator chat.

    Card layout (v1.1):
      *🗞️ Título*                 (or 📊 for rationale)

      [preview ~400 chars]

      📅 `DD/MM HH:MM UTC` · 📰 source · 🔖 tabName
      🆔 `<id>`

      [📖 Ler completo] [✅ Arquivar]
      [❌ Recusar]       [🖋️ Writer]
    """
    if not preview_base_url or not preview_base_url.startswith(("http://", "https://")):
        raise ValueError("preview_base_url must be an absolute http(s) URL")
    item_id = item.get("id")
    if not item_id:
        raise ValueError("post_for_curation requires item['id']")

    preview_url = f"{preview_base_url.rstrip('/')}/preview/{item_id}"

    title = _escape_md((item.get("title") or "").strip())
    icon = "📊" if (item.get("type") or "news") == "rationale" else "🗞️"
    header = f"*{icon} {title}*" if title else f"*{icon} (sem título)*"

    full_text = item.get("fullText") or ""
    preview = _escape_md(build_preview(full_text))

    publish_date = item.get("publishDate") or item.get("date") or ""
    source = item.get("source") or ""
    tab_name = item.get("tabName") or ""

    meta_parts = []
    if publish_date:
        meta_parts.append(f"📅 `{_escape_md(publish_date)}`")
    if source:
        meta_parts.append(f"📰 {_escape_md(source)}")
    if tab_name:
        meta_parts.append(f"🔖 {_escape_md(tab_name)}")
    meta_line = " · ".join(meta_parts) if meta_parts else ""

    body_lines = [header, "", preview]
    if meta_line:
        body_lines.append("")
        body_lines.append(meta_line)
    body_lines.append(f"🆔 `{item_id}`")
    body = "\n".join(body_lines)

    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "📖 Ler completo", "url": preview_url},
                {"text": "✅ Arquivar", "callback_data": f"curate_archive:{item_id}"},
            ],
            [
                {"text": "❌ Recusar", "callback_data": f"curate_reject:{item_id}"},
                {"text": "🖋️ Writer", "callback_data": f"curate_pipeline:{item_id}"},
            ],
        ],
    }

    TelegramClient().send_message(chat_id=chat_id, text=body, reply_markup=reply_markup)
```

- [ ] **Step 6: Run tests — expect pass**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest tests/test_curation_telegram_poster.py -v`
Expected: todos passando (número original + o novo teste de rationale).

- [ ] **Step 7: Full suite**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && pytest -q`
Expected: todos passando.

- [ ] **Step 8: Commit**

```bash
git add execution/curation/telegram_poster.py tests/test_curation_telegram_poster.py
git commit -m "feat(card): title with type icon + single-line meta + Writer button (2x2)"
```

---

## Task 10: Rename + confirmações polidas em app.py

**Files:**
- Modify: `webhook/app.py`

- [ ] **Step 1: Localizar as strings a trocar**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && grep -n "3 agents\|3 Agents\|três agent\|Enviado aos 3" webhook/app.py`

Esperado listar as ocorrências (callbacks, mensagens de progresso, answer_callback texts, finalize_card strings).

- [ ] **Step 2: Trocar no `curate_pipeline` handler**

Dentro do branch `elif action == "curate_pipeline":` (localizar com `grep -n '"curate_pipeline"' webhook/app.py`), substituir:

```python
        answer_callback(callback_id, "🤖 Processando nos 3 agents...")
        progress = send_telegram_message(chat_id, f"🤖 Processando item `{item_id}` nos 3 agents...")
```

por:

```python
        answer_callback(callback_id, "🖋️ Enviando para o Writer...")
        progress = send_telegram_message(chat_id, f"🖋️ *Enviando para o Writer*\n🆔 `{item_id}`")
```

E substituir o `finalize_card` que diz "Enviado aos 3 agents":

```python
        finalize_card(
            chat_id,
            callback_query,
            f"🤖 *Enviado aos 3 agents* em {datetime.now(timezone.utc).strftime('%H:%M')} UTC\n🆔 `{item_id}`",
        )
```

por:

```python
        finalize_card(
            chat_id,
            callback_query,
            f"🖋️ *Enviado para o Writer*\n🕒 {datetime.now(timezone.utc).strftime('%H:%M')} UTC · 🆔 `{item_id}`",
        )
```

- [ ] **Step 3: Trocar as confirmações de `curate_archive` e `curate_reject`**

Localizar `elif action == "curate_archive":` e dentro substituir:

```python
            f"✅ *Arquivado* em {datetime.now(timezone.utc).strftime('%H:%M')} UTC\n🆔 `{item_id}`",
```
por:
```python
            f"✅ *Arquivado*\n🕒 {datetime.now(timezone.utc).strftime('%H:%M')} UTC · 🆔 `{item_id}`",
```

Localizar `elif action == "curate_reject":` e dentro substituir o finalize_card atual:

```python
        finalize_card(
            chat_id,
            callback_query,
            f"❌ *Recusado* em {datetime.now(timezone.utc).strftime('%H:%M')} UTC\n"
            f"🆔 `{item_id}`\n\n"
            f"Por quê? (opcional, responda ou `pular`)",
        )
```

por:

```python
        finalize_card(
            chat_id,
            callback_query,
            f"❌ *Recusado*\n🕒 {datetime.now(timezone.utc).strftime('%H:%M')} UTC · 🆔 `{item_id}`\n\n"
            f"💭 Por quê? (opcional — responda ou `pular`)",
        )
```

- [ ] **Step 4: Trocar a confirmação do draft reject**

Localizar `elif action == "reject":` (o draft reject, não o curate_reject) e substituir seu finalize_card:

```python
        finalize_card(
            chat_id,
            callback_query,
            f"❌ *Recusado* em {datetime.now(timezone.utc).strftime('%H:%M')} UTC\n\n"
            f"Por quê? (opcional, responda ou `pular`)",
        )
```

por:

```python
        finalize_card(
            chat_id,
            callback_query,
            f"❌ *Recusado*\n🕒 {datetime.now(timezone.utc).strftime('%H:%M')} UTC\n\n"
            f"💭 Por quê? (opcional — responda ou `pular`)",
        )
```

- [ ] **Step 5: Sanity check — nenhuma string "3 agents" restante**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && grep -n "3 agents\|3 Agents\|três agent\|aos 3 agents" webhook/app.py`
Expected: 0 resultados (ou só comentários/docstrings contextuais, nunca em strings user-facing).

- [ ] **Step 6: Syntax check + full suite**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python3 -c "import ast; ast.parse(open('webhook/app.py').read()); print('syntax ok')" && pytest -q`
Expected: `syntax ok` + todos os testes passando.

- [ ] **Step 7: Commit**

```bash
git add webhook/app.py
git commit -m "feat(app): Writer rename + polished confirmations (🕒 · 🆔 one-line)"
```

---

## Task 11: Progress play-by-play (Writer → Reviewer → Finalizer)

**Files:**
- Modify: `webhook/app.py` (funções `run_3_agents` e `process_news_async`)

- [ ] **Step 1: Adicionar `on_phase_start` callback a `run_3_agents`**

Localizar `def run_3_agents(raw_text):` (hoje em webhook/app.py ~linha 853). Substituir pela versão com hook:

```python
def run_3_agents(raw_text, on_phase_start=None):
    """Run Writer → Critique → Curator chain. Returns final formatted message.

    on_phase_start: optional callable(phase_name) invoked imediatamente
    antes de cada fase. Usado para atualizar a mensagem de progresso no
    Telegram (edit_message). Nomes passados: "Writer", "Reviewer",
    "Finalizer" — nomes user-facing (não coincidem com os prompts
    internos WRITER_SYSTEM/CRITIQUE_SYSTEM/CURATOR_SYSTEM, intencional).
    """
    if on_phase_start:
        on_phase_start("Writer")
    logger.info("Agent 1/3: Writer starting...")
    writer_output = call_claude(
        WRITER_SYSTEM,
        f"Processe e analise o seguinte conteúdo do mercado de minério de ferro.\n\nCONTEÚDO:\n---\n{raw_text}\n---\n\nProduza sua análise completa."
    )
    logger.info(f"Writer done ({len(writer_output)} chars)")

    if on_phase_start:
        on_phase_start("Reviewer")
    logger.info("Agent 2/3: Critique starting...")
    critique_output = call_claude(
        CRITIQUE_SYSTEM,
        f"Revise o trabalho do Writer:\n\nTRABALHO DO WRITER:\n---\n{writer_output}\n---\n\nTEXTO ORIGINAL:\n---\n{raw_text}\n---\n\nExecute sua revisão crítica."
    )
    logger.info(f"Critique done ({len(critique_output)} chars)")

    if on_phase_start:
        on_phase_start("Finalizer")
    logger.info("Agent 3/3: Curator starting...")
    curator_output = call_claude(
        CURATOR_SYSTEM,
        f"Crie a versão final para WhatsApp.\n\nTEXTO DO WRITER:\n---\n{writer_output}\n---\n\nFEEDBACK DO CRITIQUE:\n---\n{critique_output}\n---\n\nTEXTO ORIGINAL:\n---\n{raw_text}\n---\n\nProduza APENAS a mensagem formatada."
    )
    logger.info(f"Curator done ({len(curator_output)} chars)")

    return curator_output
```

Assinatura antiga com callers passando só 1 arg continua funcionando (callback default=None → sem mudança de comportamento).

- [ ] **Step 2: Atualizar `process_news_async` pra usar o helper**

Localizar `def process_news_async(chat_id, raw_text, progress_msg_id):` e substituir pela versão que edita a mensagem de progresso em 4 estados (Writer → Reviewer → Finalizer → Draft pronto):

```python
def process_news_async(chat_id, raw_text, progress_msg_id):
    """Process news text through 3 agents in background thread."""
    from execution.core.agents_progress import format_pipeline_progress
    done = []

    def on_phase_start(phase_name):
        if progress_msg_id:
            edit_message(chat_id, progress_msg_id, format_pipeline_progress(
                current=phase_name, done=list(done),
            ))

    try:
        # Initial state before Writer
        if progress_msg_id:
            edit_message(chat_id, progress_msg_id, format_pipeline_progress(
                current="Writer", done=[],
            ))

        # Wrap on_phase_start to also update `done` as phases complete
        phase_order = ["Writer", "Reviewer", "Finalizer"]
        def hook(phase_name):
            # Mark previous phase as done (all earlier than phase_name)
            idx = phase_order.index(phase_name)
            done.clear()
            done.extend(phase_order[:idx])
            on_phase_start(phase_name)

        final_message = run_3_agents(raw_text, on_phase_start=hook)

        # Store draft
        import time
        draft_id = f"news_{int(time.time())}"
        drafts_set(draft_id, {
            "message": final_message,
            "status": "pending",
            "original_text": raw_text,
            "uazapi_token": None,
            "uazapi_url": None,
        })

        # Final "Draft pronto" state, then send approval card
        if progress_msg_id:
            edit_message(chat_id, progress_msg_id, format_pipeline_progress(
                current=None, done=list(phase_order),
            ))
        send_approval_message(chat_id, draft_id, final_message)

    except Exception as e:
        logger.error(f"process_news_async failed: {e}")
        if progress_msg_id:
            # Show error in the last-known phase
            current = None
            remaining = [p for p in ["Writer", "Reviewer", "Finalizer"] if p not in done]
            if remaining:
                current = remaining[0]
            edit_message(chat_id, progress_msg_id, format_pipeline_progress(
                current=current, done=list(done), error=str(e)[:120],
            ))
```

- [ ] **Step 3: Syntax + smoke check**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python3 -c "import ast; ast.parse(open('webhook/app.py').read()); print('syntax ok')" && pytest -q`
Expected: `syntax ok` + todos os testes passando (nenhum teste existente bate nessas funções, a regressão é captura via smoke manual em Task 12).

- [ ] **Step 4: Commit**

```bash
git add webhook/app.py
git commit -m "feat(app): 3-phase progress display (Writer/Reviewer/Finalizer) via agents_progress"
```

---

## Task 12: Manual production validation

**Files:** (nenhum arquivo — validação em produção)

- [ ] **Step 1: Push**

```bash
cd "/Users/bigode/Dev/Antigravity WF "
git push origin main
```

Aguardar Railway redeployar (~2min). Checar status:
```bash
railway service status --service web
```
Até aparecer `Status: SUCCESS`.

- [ ] **Step 2: Smoke do digest**

No Telegram, dispara um scrap manual (ou espera o próximo cron). Esperar ver UMA mensagem digest ao final (ao invés de N cards). Conferir:

- Ícone 📥 · contagem total · árvore 🗞️/📊 (ou só 1 tipo se só veio um)
- Até 3 títulos de preview com ícones por tipo
- "+N mais" se total > 3
- Botão `🔍 Abrir fila` (clica → abre /queue)

- [ ] **Step 3: Smoke do /queue novo**

Digitar `/queue`. Cada item vira 1 botão com ícone + título (não mais "N. Abrir"). Clicar em algum → card de curadoria aparece no novo layout (título com ícone no topo, meta em 1 linha, botões 2×2, botão "🖋️ Writer").

- [ ] **Step 4: Smoke do fluxo Writer**

Clicar "🖋️ Writer" num card. Conferir:

1. `answer_callback` diz "🖋️ Enviando para o Writer..."
2. Card é finalizado com "🖋️ Enviado para o Writer / 🕒 HH:MM UTC · 🆔 id"
3. Mensagem de progresso nova aparece e é editada in-place:
   - "🖋️ Writer escrevendo... (1/3)" → separador → ⏳ Writer · ⏳ Reviewer · ⏳ Finalizer
   - "🔍 Reviewer analisando... (2/3)" → ✅ Writer · ⏳ Reviewer · ⏳ Finalizer
   - "✨ Finalizer polindo... (3/3)" → ✅ Writer · ✅ Reviewer · ⏳ Finalizer
   - "✅ Draft pronto" → ✅ · ✅ · ✅
4. Draft final com card de aprovação aparece abaixo (inalterado de v1).

- [ ] **Step 5: Smoke do /stats, /history, /rejections**

Rodar cada um. Verificar:
- `/stats` — emoji por linha, separador `────`, label "🖋️ No Writer" (e não "Pipeline")
- `/history` — ícone 🗞️/📊 por item, separador
- `/rejections` — 🕒 prefix por entrada, separador

- [ ] **Step 6: Smoke do fluxo de recusa**

Recusar um item. Card finalizado deve mostrar:
```
❌ Recusado
🕒 HH:MM UTC · 🆔 <id>

💭 Por quê? (opcional — responda ou `pular`)
```

Responder razão → "✅ Razão registrada." Rodar `/rejections` → entrada aparece.

- [ ] **Step 7: Marcar milestone completa**

Se todos os 6 smokes passarem, v1.1 está entregue. Se algum falhar, capturar logs (`railway logs --service web | tail -50`) e abrir fix targeted.

---

## Follow-up (NÃO faz parte deste plano)

Abrir 2 issues/TODOs depois do merge desta fase:

1. **Bug de duplicatas no scrap**: `platts:seen:<date>` às vezes deixa passar items repetidos. Investigar `generate_id` (determinístico sobre quais campos?) e se `is_seen` consulta a chave certa.
2. **Fase de ajuste de prompts dos 3 agents**: Writer/Reviewer/Finalizer hoje têm prompts genéricos pra notícia. Rationale vai passar pelos mesmos prompts e o output pode não ficar adequado — avaliar depois do smoke e, se ruim, iniciar nova fase com prompts diferenciados.
