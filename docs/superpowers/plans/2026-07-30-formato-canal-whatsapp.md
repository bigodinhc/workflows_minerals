# Formato do canal otimizado pra cópia no WhatsApp — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fazer as mensagens do canal Telegram sobreviverem à cópia manual pro WhatsApp — bloco copiável com o texto-fonte cru, e linha de cotação sem o blockquote que afogava o destaque numérico.

**Architecture:** Um módulo novo (`execution/core/report_format.py`) centraliza header e linha dos 3 crons, que hoje duplicam a lógica. No lado do webhook, `channel_delivery.py` ganha funções puras que montam um `<pre>` com o texto-fonte intocado e decidem entre 1 ou 2 mensagens conforme o teto de 4096 do Telegram. O prompt do Curator não muda — as notícias já estão no formato-alvo.

**Tech Stack:** Python 3, aiogram 3 (lado webhook), pytest + pytest-asyncio + unittest.mock.

**Spec:** `docs/superpowers/specs/2026-07-30-formato-canal-whatsapp-design.md`

## Global Constraints

- Branch de trabalho: `feat/formato-canal-whatsapp` (já ativa, base = `origin/main` pós-PR #5).
- Testes: `.venv/bin/python -m pytest tests/<arquivo> -v` (venv da raiz). Async usa `@pytest.mark.asyncio`.
- Working tree tem arquivos sujos **não relacionados** (`actors/platts-scrap-full-news/*`, `webhook/uv.lock`, `contatos_whatsapp.xlsx`, `"[WF] RELATORIO DIARIO.json"`). **Nunca `git add -A`** — stage só os arquivos da task.
- Separador decimal dos crons **permanece ponto** (`$98.20`). Não trocar por vírgula. As notícias seguem com vírgula via Curator; a divergência é intencional (spec §3.1).
- Marcadores em **todos** os crons: `↑` alta, `↓` baixa, `·` estável. SGX e Baltic mudam (hoje `🟢`/`🔴`/`▪️`); Platts já usa setas.
- Emojis de seção dos crons **ficam** (`🪨 *FINES*`, `⚓ *ROTAS CAPESIZE*`, `🌊 *BALTIC DRY INDEX*`, `🚢 *INDICES POR TIPO*`, `🧱 *LUMP AND PELLET*`, `🧪 *VIU DIFFERENTIALS*`). Decisão do usuário (2026-07-30) — não aplicar a regra "sem emoji no corpo" do Curator aos crons.
- Header canônico literal, 4 linhas, nesta ordem exata:
  ```
  📊 *MINERALS TRADING*
  *<título>*
  `<ATIVO> · <DD/MMM>`
  ─────────────────
  ```
  A divisória é `─────────────────` (17× U+2500) e é a **única** da mensagem.
- Meses da pílula em PT-BR CAPS 3 letras: `JAN FEV MAR ABR MAI JUN JUL AGO SET OUT NOV DEZ`.
- **Não remover** `_quote_lines_to_blockquote` de `channel_delivery.py` — o Curator segue usando `> ` pra citação real.
- **Não mexer** em `execution/core/prompts/curator.py`.
- Commits convencionais em PT, sem attribution.

---

## Estrutura de arquivos

| Arquivo | Responsabilidade | Task |
|---|---|---|
| `execution/core/report_format.py` (novo) | Header, linha, marcador e tabelas de mês — fonte única dos 3 crons | 1 |
| `tests/test_report_format.py` (novo) | Cobre o módulo acima | 1 |
| `execution/scripts/send_daily_report.py` | Consome o módulo; perde tabela de meses e formatação de linha próprias | 2 |
| `execution/scripts/morning_check.py` | Consome o módulo | 3 |
| `execution/scripts/baltic_ingestion.py` | Consome o módulo | 4 |
| `webhook/bot/channel_delivery.py` | Funções puras do bloco copiável + teto corrigido | 5 |
| `webhook/bot/channel_delivery.py` | `post_report_to_channel` passa a postar N mensagens | 6 |

---

### Task 1: Módulo compartilhado `report_format`

**Files:**
- Create: `execution/core/report_format.py`
- Test: `tests/test_report_format.py`

**Interfaces:**
- Consumes: nada (folha).
- Produces (Tasks 2, 3 e 4 dependem):
  - `build_header(title: str, asset: str, when: datetime.date) -> str` — 4 linhas
  - `format_row(name: str, value: str, stats: str, marker: str) -> str` — 1 linha
  - `marker_for(change: float) -> str` — `"↑"` | `"↓"` | `"·"`
  - `translate_contract_month(month_code: str) -> str` — `"FEB/26"` → `"Fev/26"`
  - `pill_date(when: datetime.date) -> str` — `"30/JUL"`
  - Constantes: `DIVIDER`, `MARKER_UP`, `MARKER_DOWN`, `MARKER_FLAT`, `MONTHS_PT_ABBR`, `MONTHS_EN_TO_PT`

- [ ] **Step 1: Escrever os testes que falham**

Criar `tests/test_report_format.py`:

```python
"""Primitivas de renderização compartilhadas pelos 3 crons do canal."""
import sys
from datetime import date
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))


def test_pill_date_pt_br_caps():
    from execution.core.report_format import pill_date
    assert pill_date(date(2026, 7, 30)) == "30/JUL"
    assert pill_date(date(2026, 1, 5)) == "05/JAN"
    assert pill_date(date(2026, 12, 31)) == "31/DEZ"


def test_pill_date_cobre_os_12_meses():
    from execution.core.report_format import pill_date
    esperado = ["JAN", "FEV", "MAR", "ABR", "MAI", "JUN",
                "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"]
    for mes, sigla in enumerate(esperado, start=1):
        assert pill_date(date(2026, mes, 1)) == f"01/{sigla}"


def test_build_header_quatro_linhas_canonicas():
    from execution.core.report_format import build_header
    header = build_header("Baltic Exchange — Frete", "FRETE", date(2026, 7, 30))
    linhas = header.split("\n")
    assert linhas[0] == "📊 *MINERALS TRADING*"
    assert linhas[1] == "*Baltic Exchange — Frete*"
    assert linhas[2] == "`FRETE · 30/JUL`"
    assert linhas[3] == "─────────────────"
    assert len(linhas) == 4


def test_build_header_divisoria_unica():
    from execution.core.report_format import build_header, DIVIDER
    header = build_header("Qualquer", "ATIVO", date(2026, 7, 30))
    assert header.count(DIVIDER) == 1


def test_marker_for_tres_faixas():
    from execution.core.report_format import marker_for
    assert marker_for(1.5) == "↑"
    assert marker_for(-0.3) == "↓"
    assert marker_for(0) == "·"
    assert marker_for(0.0) == "·"


def test_format_row_sem_blockquote_e_sem_negrito_no_nome():
    from execution.core.report_format import format_row
    linha = format_row("Brazilian Blend Fines", "$107.90", "-0.55 (-0.51%)", "↓")
    assert linha == "Brazilian Blend Fines  `$107.90`  -0.55 (-0.51%) ↓"
    assert not linha.startswith("> ")      # blockquote tingia a linha toda
    assert "*Brazilian" not in linha       # nome fica sem negrito (Opção B)


def test_format_row_valor_e_o_unico_em_mono():
    from execution.core.report_format import format_row
    linha = format_row("BDI", "1842", "+23 (+1.26%)", "↑")
    assert linha.count("`") == 2


def test_translate_contract_month():
    from execution.core.report_format import translate_contract_month
    assert translate_contract_month("FEB/26") == "Fev/26"
    assert translate_contract_month("FEB26") == "Fev/26"
    assert translate_contract_month("aug/26") == "Ago/26"
    assert translate_contract_month("XYZ26") == "XYZ/26"  # desconhecido passa direto
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/bin/python -m pytest tests/test_report_format.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'execution.core.report_format'`

- [ ] **Step 3: Implementar o módulo**

Criar `execution/core/report_format.py`:

```python
"""Shared rendering primitives for the cron reports posted to the channel.

The three daily crons (SGX prices, Platts assessments, Baltic freight) render
the same two shapes — a 4-line header and one row per instrument. Keeping them
here means the next aesthetic change is one edit instead of three identical
ones.

Output is WhatsApp marker syntax: it renders in Telegram after
webhook/bot/channel_delivery.to_telegram_html(), and pastes into WhatsApp
unchanged. That double duty is why the markers stay in the source text.
"""

from __future__ import annotations

from datetime import date

# Pill dates: PT-BR, CAPS, 3 letters — mirrors the Curator prompt's date rule
# so cron posts and news posts read the same in the channel.
MONTHS_PT_ABBR = {
    1: "JAN", 2: "FEV", 3: "MAR", 4: "ABR", 5: "MAI", 6: "JUN",
    7: "JUL", 8: "AGO", 9: "SET", 10: "OUT", 11: "NOV", 12: "DEZ",
}

# Contract labels ('FEB/26' -> 'Fev/26'). Only daily_report has contract months.
MONTHS_EN_TO_PT = {
    "JAN": "Jan", "FEB": "Fev", "MAR": "Mar", "APR": "Abr",
    "MAY": "Mai", "JUN": "Jun", "JUL": "Jul", "AUG": "Ago",
    "SEP": "Set", "OCT": "Out", "NOV": "Nov", "DEC": "Dez",
}

DIVIDER = "─────────────────"

MARKER_UP = "↑"
MARKER_DOWN = "↓"
MARKER_FLAT = "·"


def pill_date(when: date) -> str:
    """'30/JUL' — the date half of the header pill."""
    return f"{when.day:02d}/{MONTHS_PT_ABBR[when.month]}"


def build_header(title: str, asset: str, when: date) -> str:
    """The canonical 4-line header, shared with the Curator prompt.

    📊 *MINERALS TRADING*
    *<title>*
    `<ASSET> · <DD/MMM>`
    ─────────────────
    """
    return "\n".join([
        "📊 *MINERALS TRADING*",
        f"*{title}*",
        f"`{asset} · {pill_date(when)}`",
        DIVIDER,
    ])


def translate_contract_month(month_code: str) -> str:
    """'FEB/26' or 'FEB26' -> 'Fev/26'. Unknown months pass through as-is."""
    code = month_code.upper().replace("/", "")
    return f"{MONTHS_EN_TO_PT.get(code[:3], code[:3])}/{code[3:]}"


def marker_for(change: float) -> str:
    """↑ rising, ↓ falling, · flat."""
    if change > 0:
        return MARKER_UP
    if change < 0:
        return MARKER_DOWN
    return MARKER_FLAT


def format_row(name: str, value: str, stats: str, marker: str) -> str:
    """One instrument per line: plain name, mono value, plain stats, marker.

    No '> ' prefix on purpose. A blockquote tints the whole line grey, which
    drowned the mono value — the one thing on the line meant to stand out.
    """
    return f"{name}  `{value}`  {stats} {marker}"
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `.venv/bin/python -m pytest tests/test_report_format.py -v`
Expected: PASS — 8 testes.

- [ ] **Step 5: Commit**

```bash
git add execution/core/report_format.py tests/test_report_format.py
git commit -m "feat: módulo compartilhado de header e linha dos relatórios do canal"
```

---

### Task 2: `daily_report` adota o formato novo

**Files:**
- Modify: `execution/scripts/send_daily_report.py:24-70` (`format_price_message`)
- Test: `tests/test_daily_report_format.py` (reescreve o teste existente)

**Interfaces:**
- Consumes (Task 1): `build_header`, `format_row`, `marker_for`, `translate_contract_month`.
- Produces: `format_price_message(prices) -> str` — assinatura inalterada.

Data da pílula: `datetime.now(BRT).date()` — a cotação é da sessão corrente (spec §5.1).

- [ ] **Step 1: Reescrever o teste pra o formato novo**

Substituir o conteúdo de `tests/test_daily_report_format.py`:

```python
"""Layout do relatório diário SGX (header canônico + linha Opção B)."""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))


def test_format_price_message_header_e_linhas():
    from execution.scripts.send_daily_report import format_price_message
    prices = [
        {"month": "AUG/26", "price": 103.50, "change": 0.55, "pct_change": 0.53},
        {"month": "OCT/26", "price": 102.60, "change": -0.15, "pct_change": -0.15},
        {"month": "NOV/26", "price": 102.10, "change": 0.00, "pct_change": 0.00},
    ]
    msg = format_price_message(prices)
    lines = msg.split("\n")

    assert lines[0] == "📊 *MINERALS TRADING*"
    assert lines[1] == "*Curva SGX — Iron Ore 62% Fe*"
    assert lines[2].startswith("`IRON ORE FUTURES · ")
    assert lines[2].endswith("`")
    assert lines[3] == "─────────────────"
    assert lines[4] == ""
    assert lines[5] == "Ago/26  `$103.50`  +0.55 (+0.53%) ↑"
    assert lines[6] == "Out/26  `$102.60`  -0.15 (-0.15%) ↓"
    assert lines[7] == "Nov/26  `$102.10`  estável ·"
    assert len(lines) == 8


def test_format_price_message_sem_blockquote_nem_bolinhas():
    from execution.scripts.send_daily_report import format_price_message
    msg = format_price_message(
        [{"month": "AUG/26", "price": 103.50, "change": 0.55, "pct_change": 0.53}]
    )
    assert "> " not in msg          # blockquote saiu
    assert "🟢" not in msg          # bolinhas viraram setas
    assert "🔴" not in msg
    assert "▪️" not in msg
    assert msg.count("─────────────────") == 1   # divisória única
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/bin/python -m pytest tests/test_daily_report_format.py -v`
Expected: FAIL — `lines[0]` ainda é `"📊 *MINERALS TRADING DAILY REPORT*"`.

- [ ] **Step 3: Reescrever `format_price_message`**

Em `execution/scripts/send_daily_report.py`, substituir a função inteira (linhas 24-70) por:

```python
def format_price_message(prices):
    """
    Formats the price list with backtick-highlighted prices.
    Expects 'prices' to be a list of dicts: {month, price, change, pct_change}
    """
    from datetime import timezone, timedelta

    from execution.core.report_format import (
        build_header,
        format_row,
        marker_for,
        translate_contract_month,
    )

    BRT = timezone(timedelta(hours=-3))
    today = datetime.now(BRT).date()

    lines = [build_header("Curva SGX — Iron Ore 62% Fe", "IRON ORE FUTURES", today), ""]

    for p in prices:
        change_val = float(p.get("change", 0))
        pct_val = float(p.get("pct_change", 0))
        month_pt = translate_contract_month(str(p.get("month", "???")))
        price_float = float(p.get("price", 0))

        if change_val > 0:
            stats = f"+{change_val:.2f} (+{pct_val:.2f}%)"
        elif change_val < 0:
            stats = f"{change_val:.2f} ({pct_val:.2f}%)"
        else:
            stats = "estável"

        lines.append(
            format_row(month_pt, f"${price_float:.2f}", stats, marker_for(change_val))
        )

    return "\n".join(lines)
```

Isso remove o dict local `MONTHS_PT` e a função interna `translate_month` (agora vivem no módulo compartilhado).

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `.venv/bin/python -m pytest tests/test_daily_report_format.py tests/test_send_daily_report_delivery.py -v`
Expected: PASS — os testes de entrega não tocam no formato, devem seguir verdes.

- [ ] **Step 5: Commit**

```bash
git add execution/scripts/send_daily_report.py tests/test_daily_report_format.py
git commit -m "feat(daily_report): header canônico e linha sem blockquote"
```

---

### Task 3: `morning_check` adota o formato novo

**Files:**
- Modify: `execution/scripts/morning_check.py:96-120` (`format_line`) e `:141-170` (`build_message`)
- Test: `tests/test_morning_check_format.py` (reescreve o teste existente)

**Interfaces:**
- Consumes (Task 1): `build_header`, `format_row`, `marker_for`.
- Produces: `build_message(report_items, date_str) -> str` e `format_line(item) -> str | None` — assinaturas inalteradas.

Data da pílula: parse de `date_str`, que chega no formato `%d/%m/%Y` (`morning_check.py:276` monta `date_fmt_br`). Fallback pra hoje em BRT quando não parseia.

- [ ] **Step 1: Reescrever o teste pra o formato novo**

Substituir o conteúdo de `tests/test_morning_check_format.py`:

```python
"""Layout do morning_check (header canônico + linha Opção B)."""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))


def _itens():
    return [
        {"variable_key": "IOBBA00", "product": "Brazilian Blend Fines",
         "price": 105.32, "change": 1.25, "changePercent": 1.20},
        {"variable_key": "IODFE00", "product": "IO fines Fe 58%",
         "price": 90.10, "change": -0.85, "changePercent": -0.93},
        {"variable_key": "IOPRM00", "product": "IO fines Fe 65%",
         "price": 120.00, "change": 0, "changePercent": 0},
    ]


def test_morning_check_header_e_linhas():
    from execution.scripts.morning_check import build_message

    msg = build_message(_itens(), "09/07/2026")
    lines = msg.split("\n")

    assert lines[0] == "📊 *MINERALS TRADING*"
    assert lines[1] == "*Assessments Platts — Abertura*"
    assert lines[2] == "`IRON ORE · 09/JUL`"
    assert lines[3] == "─────────────────"
    assert lines[4] == ""
    assert lines[5] == "🪨 *FINES*"
    assert lines[6] == "Brazilian Blend Fines  `$105.32`  +1.25 (+1.20%) ↑"
    assert lines[7] == "IO fines Fe 58%  `$90.10`  -0.85 (-0.93%) ↓"
    assert lines[8] == "IO fines Fe 65%  `$120.00`  estável ·"
    assert len(lines) == 9

    # Só itens de FINES_KEYS foram passados
    assert "LUMP AND PELLET" not in msg
    assert "VIU DIFFERENTIALS" not in msg
    assert "FREIGHT" not in msg


def test_morning_check_mantem_emoji_de_secao_e_perde_blockquote():
    from execution.scripts.morning_check import build_message

    msg = build_message(_itens(), "09/07/2026")
    assert "🪨 *FINES*" in msg        # emoji de seção fica (decisão do usuário)
    assert "> " not in msg            # blockquote saiu
    assert msg.count("─────────────────") == 1


def test_morning_check_data_invalida_cai_pra_hoje():
    from datetime import datetime, timezone, timedelta
    from execution.scripts.morning_check import build_message
    from execution.core.report_format import pill_date

    msg = build_message(_itens(), "data-quebrada")
    hoje = datetime.now(timezone(timedelta(hours=-3))).date()
    assert f"`IRON ORE · {pill_date(hoje)}`" in msg
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/bin/python -m pytest tests/test_morning_check_format.py -v`
Expected: FAIL — `lines[0]` ainda é `"📊 *MINERALS TRADING DAILY REPORT*"`.

- [ ] **Step 3: Reescrever `format_line` e o header de `build_message`**

Em `execution/scripts/morning_check.py`, substituir `format_line` (linhas 96-120) por:

```python
def format_line(item):
    """Formats a single item line via the shared row renderer."""
    if not item: return None

    from execution.core.report_format import format_row, marker_for

    desc = item.get('product', 'Unknown')
    price = item['price']
    change = item.get('change', 0)
    pct = item.get('changePercent', 0)

    if change > 0:
        stats = f"+{change:.2f} (+{pct:.2f}%)"
    elif change < 0:
        stats = f"{change:.2f} ({pct:.2f}%)"
    else:
        stats = "estável"

    return format_row(desc, f"${price:.2f}", stats, marker_for(change))
```

Nota: a variável local `assess_type` era calculada e nunca usada — sai junto.

Adicionar o helper de data logo acima de `build_message`:

```python
def _pill_date_from(date_str):
    """'09/07/2026' -> date. Cai pra hoje (BRT) quando não parseia."""
    from datetime import datetime as _dt, timezone, timedelta
    try:
        return _dt.strptime(date_str, "%d/%m/%Y").date()
    except (ValueError, TypeError):
        return _dt.now(timezone(timedelta(hours=-3))).date()
```

E em `build_message`, trocar a linha 155:

```python
    header = f"📊 *MINERALS TRADING DAILY REPORT*\n🔍 IRON ORE MARKET UPDATE - {date_str}"
```

por:

```python
    from execution.core.report_format import build_header
    header = build_header(
        "Assessments Platts — Abertura", "IRON ORE", _pill_date_from(date_str)
    )
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `.venv/bin/python -m pytest tests/test_morning_check_format.py tests/test_morning_check_delivery.py tests/test_morning_check_idempotency.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add execution/scripts/morning_check.py tests/test_morning_check_format.py
git commit -m "feat(morning_check): header canônico e linha sem blockquote"
```

---

### Task 4: `baltic_ingestion` adota o formato novo

**Files:**
- Modify: `execution/scripts/baltic_ingestion.py:71-166` (`format_whatsapp_message`)
- Test: `tests/test_baltic_format.py` (reescreve o teste existente)

**Interfaces:**
- Consumes (Task 1): `build_header`, `format_row`, `marker_for`, `MARKER_FLAT`.
- Produces: `format_whatsapp_message(data) -> str` — assinatura inalterada.

Data da pílula: parse de `data['report_date']` (formato `%Y-%m-%d`), fallback pra hoje em BRT.

- [ ] **Step 1: Reescrever o teste pra o formato novo**

Substituir o conteúdo de `tests/test_baltic_format.py`:

```python
"""Layout do baltic_ingestion (header canônico + linha Opção B + setas)."""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))


def _dados():
    return {
        "report_date": "2026-07-09",
        "bdi": {"value": 1850, "change": 25},
        "capesize": {"value": 2100, "change": -50},
        "panamax": {"value": 1200, "change": 0},
        "supramax": {"value": 800, "change": 10},
        "handysize": {"value": 500, "change": -5},
        "routes": [
            {"code": "C3", "value": 25.50, "change": -0.30},
            {"code": "C5", "value": 10.20, "change": 0},
            {"code": "C5TC", "value": 15000, "change": 200},
        ],
    }


def test_baltic_header_e_linhas():
    from execution.scripts.baltic_ingestion import format_whatsapp_message

    msg = format_whatsapp_message(_dados())
    lines = msg.split("\n")

    assert lines[0] == "📊 *MINERALS TRADING*"
    assert lines[1] == "*Baltic Exchange — Frete*"
    assert lines[2] == "`FRETE · 09/JUL`"
    assert lines[3] == "─────────────────"
    assert lines[4] == ""
    assert lines[5] == "🌊 *BALTIC DRY INDEX*"
    assert lines[6] == "BDI  `1850`  +25 (+1.37%) ↑"
    assert lines[7] == ""
    assert lines[8] == "⚓ *ROTAS CAPESIZE*"
    assert lines[9] == "C3 Tubarao → Qingdao  `$25.50/ton`  -0.30 (-1.16%) ↓"
    assert lines[10] == "C5 W.Australia → Qingdao  `$10.20/ton`  estável ·"
    assert lines[11] == "C5TC Timecharter Avg  `15000/day`  +200 (+1.35%) ↑"
    assert lines[12] == ""
    assert lines[13] == "🚢 *INDICES POR TIPO*"
    assert lines[14] == "Capesize (100k+ DWT)  `2100`  -50 (-2.33%) ↓"
    assert lines[15] == "Panamax (60-80k DWT)  `1200`  estável ·"
    assert lines[16] == "Supramax (45-60k DWT)  `800`  +10 (+1.27%) ↑"
    assert lines[17] == "Handysize (15-35k DWT)  `500`  -5 (-0.99%) ↓"
    assert len(lines) == 18

    # Rotas C2/C7 ausentes do input → linhas puladas
    assert "C2 Tubarao" not in msg
    assert "C7 Bolivar" not in msg


def test_baltic_sem_blockquote_nem_bolinhas():
    from execution.scripts.baltic_ingestion import format_whatsapp_message

    msg = format_whatsapp_message(_dados())
    assert "> " not in msg
    assert "🟢" not in msg
    assert "🔴" not in msg
    assert "▪️" not in msg
    assert msg.count("─────────────────") == 1


def test_baltic_data_invalida_cai_pra_hoje():
    from datetime import datetime, timezone, timedelta
    from execution.scripts.baltic_ingestion import format_whatsapp_message
    from execution.core.report_format import pill_date

    dados = {**_dados(), "report_date": ""}
    msg = format_whatsapp_message(dados)
    hoje = datetime.now(timezone(timedelta(hours=-3))).date()
    assert f"`FRETE · {pill_date(hoje)}`" in msg
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/bin/python -m pytest tests/test_baltic_format.py -v`
Expected: FAIL — `lines[0]` ainda é `"📊 *MINERALS TRADING DAILY REPORT*"`.

- [ ] **Step 3: Reescrever o header e a `format_line` interna**

Em `execution/scripts/baltic_ingestion.py`, dentro de `format_whatsapp_message`, substituir o bloco de data (linhas 95-101):

```python
    # Format date as DD/MM/YYYY
    report_date = data.get('report_date', '')
    try:
        dt = datetime.strptime(report_date, '%Y-%m-%d')
        date_formatted = dt.strftime('%d/%m/%Y')
    except:
        date_formatted = report_date
```

por:

```python
    # Pill date: the Baltic email's own report date, not the run date — the
    # email often lands the morning after the session it reports.
    from datetime import timezone, timedelta
    try:
        pill_when = datetime.strptime(data.get('report_date', ''), '%Y-%m-%d').date()
    except (ValueError, TypeError):
        pill_when = datetime.now(timezone(timedelta(hours=-3))).date()
```

Substituir a `format_line` interna (linhas 103-131) por:

```python
    def format_line(name, value, change, unit="", decimals=2, is_index=False):
        """Format a single data line via the shared row renderer."""
        from execution.core.report_format import format_row, marker_for, MARKER_FLAT

        if is_index:
            val_str = f"{int(value)}" if value else "N/A"
            chg_str = format_change(change, 0)
        else:
            val_str = f"${value:.{decimals}f}" if value else "N/A"
            chg_str = format_change(change, decimals)

        if change == 0 or not change:
            return format_row(name, f"{val_str}{unit}", "estável", MARKER_FLAT)

        try:
            change_val = float(change)
        except (TypeError, ValueError):
            change_val = 0

        try:
            pct = (float(change) / (float(value) - float(change))) * 100
            pct_str = f"({pct:+.2f}%)"
        except (TypeError, ValueError, ZeroDivisionError):
            pct_str = ""

        stats = f"{chg_str} {pct_str}".strip()
        return format_row(name, f"{val_str}{unit}", stats, marker_for(change_val))
```

Substituir o header (linhas 136-137):

```python
    lines.append("📊 *MINERALS TRADING DAILY REPORT*")
    lines.append(f"🚢 BALTIC EXCHANGE UPDATE - {date_formatted}")
```

por:

```python
    from execution.core.report_format import build_header
    lines.append(build_header("Baltic Exchange — Frete", "FRETE", pill_when))
```

O `lines.append("")` que já vem logo depois permanece.

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `.venv/bin/python -m pytest tests/test_baltic_format.py tests/test_baltic_delivery.py tests/test_baltic_ingestion_idempotency.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add execution/scripts/baltic_ingestion.py tests/test_baltic_format.py
git commit -m "feat(baltic): header canônico, linha sem blockquote e setas"
```

---

### Task 5: Funções puras do bloco copiável + teto corrigido

**Files:**
- Modify: `webhook/bot/channel_delivery.py:22-38` (constantes) — acrescenta constantes novas e corrige `RAW_TEXT_LIMIT`
- Modify: `webhook/bot/channel_delivery.py` — acrescenta 3 funções puras após `to_telegram_html`
- Test: `tests/test_channel_delivery.py` (acrescenta casos; não remove os 8 existentes)

**Interfaces:**
- Consumes: `escape_html`, `to_telegram_html`, `_BOLD_RE`, `_CODE_RE`, `_ITALIC_RE` (já existem no módulo).
- Produces (Task 6 depende):
  - `has_whatsapp_markers(text: str) -> bool`
  - `build_copy_block(raw: str) -> str`
  - `build_channel_payload(raw: str) -> list[str]` — 1 ou 2 elementos
  - Constantes: `TELEGRAM_TEXT_LIMIT = 4096`, `COPY_LABEL = "📋 Copiar pro WhatsApp:"`

- [ ] **Step 1: Escrever os testes que falham**

Acrescentar ao fim de `tests/test_channel_delivery.py`:

```python
# ── bloco copiável pro WhatsApp ──

def test_has_whatsapp_markers_detecta_cada_marcador():
    from bot.channel_delivery import has_whatsapp_markers
    assert has_whatsapp_markers("*IRON ORE*")
    assert has_whatsapp_markers("preço `$107.90` hoje")
    assert has_whatsapp_markers("_em alta_")
    assert has_whatsapp_markers("> citação do CEO")
    assert has_whatsapp_markers("linha 1\n> citação")


def test_has_whatsapp_markers_falso_sem_marcador():
    from bot.channel_delivery import has_whatsapp_markers
    assert not has_whatsapp_markers("📄 relatorio-platts-30-07.pdf")
    assert not has_whatsapp_markers("")
    assert not has_whatsapp_markers("texto puro sem formatação")


def test_build_copy_block_preserva_o_texto_cru():
    from bot.channel_delivery import build_copy_block, COPY_LABEL
    raw = "📊 *MINERALS TRADING*\nBDI  `1850`  +25 ↑"
    bloco = build_copy_block(raw)
    assert bloco.startswith(COPY_LABEL)
    assert "<pre>" in bloco and "</pre>" in bloco
    # os marcadores sobrevivem literalmente — é o ponto do bloco
    assert "*MINERALS TRADING*" in bloco
    assert "`1850`" in bloco


def test_build_copy_block_escapa_html():
    from bot.channel_delivery import build_copy_block
    bloco = build_copy_block("lucro > custo & margem < 10%")
    assert "&gt;" in bloco and "&amp;" in bloco and "&lt;" in bloco


def test_build_channel_payload_uma_mensagem_quando_cabe():
    from bot.channel_delivery import build_channel_payload, COPY_LABEL
    partes = build_channel_payload("📊 *MINERALS TRADING*\nBDI  `1850`  +25 ↑")
    assert len(partes) == 1
    assert "<b>MINERALS TRADING</b>" in partes[0]   # parte bonita
    assert COPY_LABEL in partes[0]                   # bloco junto


def test_build_channel_payload_divide_quando_estoura():
    from bot.channel_delivery import build_channel_payload, COPY_LABEL, TELEGRAM_TEXT_LIMIT
    # 60 linhas de cotação com marcador → pretty + bloco passam de 4096
    raw = "\n".join(f"*Produto {i}*  `$10{i}.50`  +0.55 (+0.53%) ↑" for i in range(60))
    partes = build_channel_payload(raw)
    assert len(partes) == 2
    assert COPY_LABEL not in partes[0]     # 1ª é só o post legível
    assert partes[1].startswith(COPY_LABEL)
    assert all(len(p) <= TELEGRAM_TEXT_LIMIT for p in partes)


def test_build_channel_payload_pula_bloco_sem_marcadores():
    from bot.channel_delivery import build_channel_payload, COPY_LABEL
    partes = build_channel_payload("📄 relatorio-platts-30-07.pdf")
    assert len(partes) == 1
    assert COPY_LABEL not in partes[0]


def test_build_channel_payload_nunca_trunca_conteudo():
    from bot.channel_delivery import build_channel_payload
    raw = "\n".join(f"*Produto {i}*  `$10{i}.50`  +0.55 (+0.53%) ↑" for i in range(60))
    partes = build_channel_payload(raw)
    # toda linha do original aparece em alguma das partes
    for i in range(60):
        assert f"Produto {i}" in "".join(partes)


def test_raw_text_limit_cabe_no_teto_com_overhead_de_tags():
    from bot.channel_delivery import RAW_TEXT_LIMIT, TELEGRAM_TEXT_LIMIT
    # overhead medido nas mensagens reais: ~21%
    assert RAW_TEXT_LIMIT * 1.21 <= TELEGRAM_TEXT_LIMIT
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/bin/python -m pytest tests/test_channel_delivery.py -v`
Expected: FAIL — `ImportError: cannot import name 'has_whatsapp_markers'`.

- [ ] **Step 3: Implementar**

Em `webhook/bot/channel_delivery.py`, trocar a constante `RAW_TEXT_LIMIT` (linhas 23-25):

```python
# Telegram caps message text at 4096 chars counting HTML tags; converting
# adds tag overhead, so we truncate the raw input with headroom first.
RAW_TEXT_LIMIT = 3500
```

por:

```python
TELEGRAM_TEXT_LIMIT = 4096
# Telegram counts HTML tags against the cap and converting adds ~21% on real
# messages, so the raw input is truncated with headroom first: 3300 × 1.21
# still clears 4096. At the previous 3500 a full-length post converted to
# ~4230 and Telegram would have rejected it.
RAW_TEXT_LIMIT = 3300
COPY_LABEL = "📋 Copiar pro WhatsApp:"
```

Acrescentar as 3 funções logo após `to_telegram_html` (depois da linha 75):

```python
def has_whatsapp_markers(text: str) -> bool:
    """True when the source carries markup worth preserving for a paste.

    Keeps the copy block off posts that gain nothing from it — the
    platts_reports post is a bare '📄 filename' next to a PDF attachment.
    """
    if not text:
        return False
    if _BOLD_RE.search(text) or _CODE_RE.search(text) or _ITALIC_RE.search(text):
        return True
    return text.lstrip().startswith("> ") or "\n> " in text


def build_copy_block(raw: str) -> str:
    """Label plus a <pre> block holding the raw source, HTML-escaped.

    Telegram renders <pre> with a copy affordance, and what lands on the
    clipboard is the literal marker syntax — which is what WhatsApp parses.
    Copying the rendered post instead would paste as flat text.
    """
    return f"{COPY_LABEL}\n<pre>{escape_html(raw)}</pre>"


def build_channel_payload(raw: str) -> list[str]:
    """Messages to post, in order.

    One element when the readable post and its copy block fit a single
    message; two when they don't. Content is never truncated — a long report
    costs an extra message rather than losing rows.
    """
    pretty = to_telegram_html(raw)
    if not has_whatsapp_markers(raw):
        return [pretty]

    copy_block = build_copy_block(raw)
    if len(copy_block) > TELEGRAM_TEXT_LIMIT:
        # Escaping blew the cap on its own — ship the readable post rather
        # than a block Telegram would reject outright.
        return [pretty]

    combined = f"{pretty}\n\n{copy_block}"
    if len(combined) <= TELEGRAM_TEXT_LIMIT:
        return [combined]
    return [pretty, copy_block]
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `.venv/bin/python -m pytest tests/test_channel_delivery.py -v`
Expected: PASS — 8 testes antigos + 9 novos.

- [ ] **Step 5: Commit**

```bash
git add webhook/bot/channel_delivery.py tests/test_channel_delivery.py
git commit -m "feat(canal): bloco copiável pro WhatsApp e teto de truncamento corrigido"
```

---

### Task 6: `post_report_to_channel` posta as N mensagens

**Files:**
- Modify: `webhook/bot/channel_delivery.py:91-157` (`post_report_to_channel`)
- Test: `tests/test_channel_delivery.py` (acrescenta casos)

**Interfaces:**
- Consumes (Task 5): `build_channel_payload`.
- Produces: `post_report_to_channel(...) -> dict` — contrato `{"ok", "message_id", "error"}` preservado. No split, `message_id` é o da **primeira** mensagem. Falha ao postar o bloco mantém `ok=True` e registra em `error`.

- [ ] **Step 1: Escrever os testes que falham**

Acrescentar ao fim de `tests/test_channel_delivery.py`:

```python
@pytest.mark.asyncio
async def test_post_envia_duas_mensagens_quando_divide(channel, mock_bot):
    raw = "\n".join(f"*Produto {i}*  `$10{i}.50`  +0.55 (+0.53%) ↑" for i in range(60))
    result = await channel.post_report_to_channel(raw)
    assert result["ok"] is True
    assert mock_bot.send_message.await_count == 2
    # message_id é o da primeira mensagem (o post legível)
    assert result["message_id"] == 42


@pytest.mark.asyncio
async def test_post_envia_uma_mensagem_quando_cabe(channel, mock_bot):
    result = await channel.post_report_to_channel("📊 *MINERALS TRADING*\nBDI  `1850`  +25 ↑")
    assert result["ok"] is True
    assert mock_bot.send_message.await_count == 1
    assert channel.COPY_LABEL in mock_bot.send_message.await_args.args[1]


@pytest.mark.asyncio
async def test_post_falha_do_bloco_nao_derruba_o_post(channel, mock_bot):
    from unittest.mock import MagicMock
    mock_bot.send_message.side_effect = [
        MagicMock(message_id=42),
        RuntimeError("boom no bloco"),
    ]
    raw = "\n".join(f"*Produto {i}*  `$10{i}.50`  +0.55 (+0.53%) ↑" for i in range(60))
    result = await channel.post_report_to_channel(raw)
    assert result["ok"] is True            # o principal foi entregue
    assert result["message_id"] == 42
    assert "copy_block_failed" in result["error"]


@pytest.mark.asyncio
async def test_post_falha_da_primeira_mensagem_e_erro(channel, mock_bot):
    mock_bot.send_message.side_effect = RuntimeError("telegram fora do ar")
    result = await channel.post_report_to_channel("📊 *MINERALS TRADING*")
    assert result["ok"] is False
    assert result["message_id"] is None
    assert "telegram fora do ar" in result["error"]
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/bin/python -m pytest tests/test_channel_delivery.py -k "post_envia or post_falha" -v`
Expected: FAIL — `send_message.await_count` é 1 no caso do split (a função ainda manda uma só).

- [ ] **Step 3: Reescrever o miolo de `post_report_to_channel`**

Substituir o bloco de setup + envio (linhas 113-131) por:

```python
    try:
        bot = get_bot()
        parts = build_channel_payload(message[:RAW_TEXT_LIMIT])
    except Exception as exc:
        logger.error(f"post_report_to_channel setup failed: {exc}")
        return {"ok": False, "message_id": None, "error": str(exc)[:300]}

    try:
        sent = await _call_with_flood_retry(lambda: bot.send_message(
            TELEGRAM_CLIENT_CHANNEL_ID,
            parts[0],
            parse_mode="HTML",
            disable_notification=silent,
        ))
    except Exception as exc:
        logger.error(f"post_report_to_channel send_message failed: {exc}")
        return {"ok": False, "message_id": None, "error": str(exc)[:300]}

    result = {"ok": True, "message_id": sent.message_id, "error": None}

    # The copy block is an accessory: losing it must not fail a report that
    # already reached the channel (same posture as the PDF below).
    for extra in parts[1:]:
        try:
            await _call_with_flood_retry(lambda: bot.send_message(
                TELEGRAM_CLIENT_CHANNEL_ID,
                extra,
                parse_mode="HTML",
                disable_notification=True,
            ))
        except Exception as exc:
            logger.error(f"post_report_to_channel copy block failed: {exc}")
            result = {**result, "error": f"copy_block_failed: {str(exc)[:200]}"}
```

O restante da função (envio do PDF e `pin`) fica inalterado.

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `.venv/bin/python -m pytest tests/test_channel_delivery.py -v`
Expected: PASS — 8 antigos + 9 da Task 5 + 4 novos.

- [ ] **Step 5: Rodar a suíte inteira**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS. Se algo fora dos arquivos deste plano quebrar, investigar antes de commitar — pode ser acoplamento não mapeado.

- [ ] **Step 6: Commit**

```bash
git add webhook/bot/channel_delivery.py tests/test_channel_delivery.py
git commit -m "feat(canal): post divide em duas mensagens quando o bloco não cabe"
```

---

### Task 7: Smoke no canal real e limpeza

**Files:**
- Nenhum arquivo de produção. Usa o canal `-1004478765840`.

**Interfaces:**
- Consumes: tudo das Tasks 1-6.
- Produces: validação humana antes do merge.

Contexto: o canal tem 4 membros (piloto). O smoke da fase de design deixou as mensagens **139-148** lá, todas rotuladas `🧪 TESTE`. Elas precisam ser apagadas ao fim.

- [ ] **Step 1: Gerar as 3 mensagens localmente e conferir o texto**

```bash
.venv/bin/python -c "
import sys; sys.path.insert(0,'.'); sys.path.insert(0,'webhook')
from execution.scripts.send_daily_report import format_price_message
from bot.channel_delivery import build_channel_payload
msg = format_price_message([
  {'month':'AUG/26','price':103.50,'change':0.55,'pct_change':0.53},
  {'month':'OCT/26','price':102.60,'change':-0.15,'pct_change':-0.15},
])
print(msg)
print('--- partes:', [len(p) for p in build_channel_payload(msg)])
"
```

Expected: header de 4 linhas, linhas sem `> `, marcadores `↑`/`↓`, uma parte só.

- [ ] **Step 2: Postar os 3 relatórios no canal, rotulados como teste**

Criar `/tmp/smoke_formato.py` (arquivo temporário, não vai pro repo):

```python
"""Posta os 3 relatórios no canal com o formato novo, pra validação humana."""
import json
import sys
import urllib.request

sys.path.insert(0, ".")
sys.path.insert(0, "webhook")

from bot.channel_delivery import build_channel_payload
from execution.scripts.send_daily_report import format_price_message
from execution.scripts import morning_check as mc
from execution.scripts import baltic_ingestion as bi
from execution.integrations.platts_client import PlattsClient

CHANNEL_ID = "-1004478765840"
BANNER = "🧪 *TESTE DE FORMATO* — ignore\n\n"


def token():
    for line in open(".env"):
        if line.startswith("TELEGRAM_BOT_TOKEN="):
            return line.split("=", 1)[1].strip().strip("\"'")
    raise RuntimeError("TELEGRAM_BOT_TOKEN não está no .env")


def send(tok, text):
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{tok}/sendMessage",
        data=json.dumps({
            "chat_id": CHANNEL_ID, "text": text,
            "parse_mode": "HTML", "disable_notification": True,
        }).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


daily = format_price_message([
    {"month": "AUG/26", "price": 103.50, "change": 0.55, "pct_change": 0.53},
    {"month": "OCT/26", "price": 102.60, "change": -0.15, "pct_change": -0.15},
    {"month": "NOV/26", "price": 102.10, "change": 0.00, "pct_change": 0.00},
])

keys = mc.FINES_KEYS + mc.LUMP_PELLET_KEYS + mc.VIU_KEYS
morning = mc.build_message([
    {"variable_key": k, "product": PlattsClient.SYMBOLS_DETAILS.get(k, k),
     "unit": "$/dmt", "assessmentType": "$/dmt",
     "price": 107.90, "change": -0.55, "changePercent": -0.51}
    for k in keys
], "30/07/2026")

baltic = bi.format_whatsapp_message({
    "report_date": "2026-07-30",
    "bdi": {"value": 1842, "change": 23},
    "capesize": {"value": 2901, "change": 55},
    "panamax": {"value": 1503, "change": -12},
    "supramax": {"value": 1204, "change": 8},
    "handysize": {"value": 701, "change": 0},
    "routes": [
        {"code": "C3", "value": 21.40, "change": -0.30},
        {"code": "C5", "value": 9.85, "change": 0.10},
        {"code": "C5TC", "value": 24050, "change": 460},
    ],
})

tok = token()
enviados = []
for nome, raw in [("daily_report", daily), ("morning_check", morning), ("baltic", baltic)]:
    partes = build_channel_payload(BANNER + raw)
    print(f"\n=== {nome}: {len(partes)} mensagem(ns) ===")
    for parte in partes:
        r = send(tok, parte)
        if r.get("ok"):
            enviados.append(r["result"]["message_id"])
            print(f"  {len(parte)} chars → id={r['result']['message_id']}")
        else:
            print(f"  FALHOU: {r}")

print(f"\nIDS_DO_SMOKE = {enviados}")
```

Run: `.venv/bin/python /tmp/smoke_formato.py`

**Anotar o `IDS_DO_SMOKE` impresso** — o Step 4 precisa dele.

Confirmar no canal:
- header de 4 linhas com pílula em PT-BR (`30/JUL`)
- linhas sem painel cinza; só o preço em pastilha mono
- marcadores `↑ ↓ ·`
- botão de copiar no bloco `<pre>`
- `morning_check` chegando em 2 mensagens; `daily_report` e `baltic` em 1

- [ ] **Step 3: Validar a cola no WhatsApp**

Copiar o bloco de cada uma e colar no WhatsApp. Confirmar: título em negrito, preço em pastilha mono, nenhum asterisco ou crase literal sobrando.

**Este é o critério de aceite do plano inteiro.** Se a cola não preservar a formatação, parar e reavaliar antes do merge.

- [ ] **Step 4: Apagar as mensagens de teste**

Criar `/tmp/limpa_smoke.py`:

```python
"""Apaga as mensagens de teste do canal."""
import json
import urllib.request

CHANNEL_ID = "-1004478765840"

# 139-148: smoke da fase de design (2026-07-30).
# Acrescentar aqui os ids impressos como IDS_DO_SMOKE no Step 2.
IDS = list(range(139, 149))

TOKEN = next(
    line.split("=", 1)[1].strip().strip("\"'")
    for line in open(".env")
    if line.startswith("TELEGRAM_BOT_TOKEN=")
)

for mid in IDS:
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TOKEN}/deleteMessage",
        data=json.dumps({"chat_id": CHANNEL_ID, "message_id": mid}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            print(mid, json.load(resp).get("ok"))
    except urllib.error.HTTPError as exc:
        print(mid, "falhou:", json.load(exc).get("description"))
```

Run: `.venv/bin/python /tmp/limpa_smoke.py`

Nota: o Telegram só deixa o bot apagar mensagens com menos de 48h. As do design (139-148) foram postadas em 2026-07-30 — dentro da janela se o smoke rodar até 01/08. Passado o prazo, o `deleteMessage` retorna `message can't be deleted` e a limpeza precisa ser manual no app.

- [ ] **Step 5: Atualizar o status do spec**

Em `docs/superpowers/specs/2026-07-30-formato-canal-whatsapp-design.md`, trocar a linha de status:

```markdown
- **Status:** Spec — aguardando revisão do usuário
```

por:

```markdown
- **Status:** Implementado (plano: docs/superpowers/plans/2026-07-30-formato-canal-whatsapp.md); validado por smoke no canal + cola no WhatsApp
```

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/specs/2026-07-30-formato-canal-whatsapp-design.md
git commit -m "docs: marca spec do formato do canal como implementado"
```

---

## Verificação final

- [ ] `.venv/bin/python -m pytest tests/ -q` — suíte inteira verde
- [ ] Os 3 crons publicam com header de 4 linhas e linha sem blockquote
- [ ] `morning_check` divide em 2 mensagens; `daily_report` e `baltic` em 1
- [ ] `platts_reports` (`📄 nome.pdf`) **não** ganha bloco copiável
- [ ] Cola no WhatsApp preserva negrito e mono
- [ ] Mensagens de teste apagadas do canal
- [ ] Nenhum arquivo não relacionado no diff: `git diff origin/main --stat`
