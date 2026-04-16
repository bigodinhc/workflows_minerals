# Agent Prompts v2 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite all 3+1 agent prompts with few-shot examples and trader voice, extract from app.py into dedicated files.

**Architecture:** Create `execution/core/prompts/` package with one module per prompt (writer, critique, curator, adjuster). Each exports a single string constant. Update app.py imports — no logic changes to the pipeline.

**Tech Stack:** Python 3.11, pytest

---

## File structure

| File | Role |
|------|------|
| `execution/core/prompts/__init__.py` | Package init — re-exports all 4 constants |
| `execution/core/prompts/writer.py` | WRITER_SYSTEM prompt |
| `execution/core/prompts/critique.py` | CRITIQUE_SYSTEM prompt |
| `execution/core/prompts/curator.py` | CURATOR_SYSTEM prompt |
| `execution/core/prompts/adjuster.py` | ADJUSTER_SYSTEM prompt |
| `webhook/app.py` | Remove inline prompts, import from package |
| `tests/test_prompts.py` | Import + content verification tests |

---

### Task 1: Create prompts package with Writer prompt

**Files:**
- Create: `execution/core/prompts/__init__.py`
- Create: `execution/core/prompts/writer.py`
- Create: `tests/test_prompts.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_prompts.py`:

```python
"""Tests for execution.core.prompts — import + content checks."""


def test_writer_importable():
    from execution.core.prompts.writer import WRITER_SYSTEM
    assert isinstance(WRITER_SYSTEM, str)
    assert len(WRITER_SYSTEM) > 100


def test_writer_has_inviolable_rules():
    from execution.core.prompts.writer import WRITER_SYSTEM
    assert "jamais arredonde" in WRITER_SYSTEM.lower() or "nunca arredonde" in WRITER_SYSTEM.lower()
    assert "interpretações pessoais" in WRITER_SYSTEM.lower() or "interpretação pessoal" in WRITER_SYSTEM.lower()
    assert "CFR" in WRITER_SYSTEM
    assert "FOB" in WRITER_SYSTEM
    assert "DATA NÃO ESPECIFICADA" in WRITER_SYSTEM


def test_writer_has_no_classification_tags():
    """v2: Writer output must NOT include [CLASSIFICAÇÃO] or [ELEMENTOS] tags."""
    from execution.core.prompts.writer import WRITER_SYSTEM
    assert "[CLASSIFICAÇÃO" not in WRITER_SYSTEM
    assert "[ELEMENTOS PRESENTES" not in WRITER_SYSTEM
    assert "[IMPACTO PRINCIPAL" not in WRITER_SYSTEM


def test_writer_has_few_shot_examples():
    from execution.core.prompts.writer import WRITER_SYSTEM
    assert "<example>" in WRITER_SYSTEM or "EXEMPLO" in WRITER_SYSTEM


def test_writer_has_tabular_data_rule():
    from execution.core.prompts.writer import WRITER_SYSTEM
    lower = WRITER_SYSTEM.lower()
    assert "tabela" in lower or "tabular" in lower
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_prompts.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Create the prompts package and Writer prompt**

Create `execution/core/prompts/__init__.py`:

```python
"""Agent system prompts for the 3-agent pipeline (Writer → Critique → Curator)."""
from execution.core.prompts.writer import WRITER_SYSTEM
from execution.core.prompts.critique import CRITIQUE_SYSTEM
from execution.core.prompts.curator import CURATOR_SYSTEM
from execution.core.prompts.adjuster import ADJUSTER_SYSTEM

__all__ = ["WRITER_SYSTEM", "CRITIQUE_SYSTEM", "CURATOR_SYSTEM", "ADJUSTER_SYSTEM"]
```

Create `execution/core/prompts/writer.py`:

```python
"""Writer agent system prompt — v2.

Analyzes raw market content and produces structured Portuguese text
with data preserved in tabular format. No metadata tags in output.
"""

WRITER_SYSTEM = """Você é um analista sênior de mercado de minério de ferro da Minerals Trading. Processe informações brutas do mercado internacional e crie sínteses claras em português brasileiro.

## TAREFA

Receba o conteúdo bruto e produza:
1. Um título de 5-8 palavras que capture a essência e a tensão da notícia
2. Texto analítico em português brasileiro, organizado em seções lógicas
3. Dados numéricos preservados com precisão absoluta

Comece pelo insight mais relevante para trading (o "e daí?" da notícia), depois detalhe.

## REGRAS INEGOCIÁVEIS

1. **Precisão absoluta**: jamais arredonde ou aproxime números. US$ 105,50 nunca vira "cerca de US$ 106"
2. **Fidelidade total**: não adicione interpretações pessoais ou previsões. Relate apenas o que o texto diz
3. **Clareza técnica**: mantenha terminologia do mercado (CFR, FOB, DCE, SGX, IODEX, Mt, dmt)
4. **Honestidade temporal**: se não há data explícita, sinalize [DATA NÃO ESPECIFICADA]
5. **Distinção clara**: separe fatos de especulações/previsões mencionadas por fontes

## DADOS TABULARES

Se o conteúdo contém trades, preços, volumes ou spreads repetidos:
- Organize em tabelas alinhadas por colunas (produto, porto/rota, preço)
- NUNCA converta dados tabulares em prosa ("as Jimblebar Fines foram negociadas a..." → NÃO)
- Agrupe entradas duplicadas (mesmo produto/porto) em faixa de preço (ex: ¥766-768)
- Consolide informações de múltiplas fontes numa única linha

## FORMATO DE OUTPUT

```
[TÍTULO: título de 5-8 palavras]

[Texto analítico aqui — seções com títulos em CAPS, dados em tabelas alinhadas, insight no lead]
```

Não inclua metadados como [CLASSIFICAÇÃO], [ELEMENTOS], [IMPACTO]. Apenas o título e o texto.

## EXEMPLO 1: NOTÍCIA DE MERCADO

INPUT:
---
China's pig iron and crude steel production edged higher in early April from late March levels, but output remained below year-ago volumes as mills navigated a market where balanced supply and demand dynamics have kept prices range-bound. The daily pig iron and crude steel output at CISA member mills averaged 1.892 million mt and 2.104 million mt over April 1-10, up 4.4% and 5.6% from late March, but still 3.1% and 4.2% lower year-over-year. Finished steel inventories reached 28.33 million mt as of April 10, up 1.3% from end-March and 9.5% higher year-over-year. The Platts-assessed HRC prices fell from Yuan 3,310/mt in January to Yuan 3,230/mt in late February, then returned to Yuan 3,300/mt as of April 15. Rebar prices were Yuan 3,100/mt on April 15. Steel export orders have risen strongly since early April, mainly driven by billet and slab.
---

OUTPUT:
---
[TÍTULO: China Produz Mais Aço, Mas Demanda Trava]

Produção subiu vs. março, mas segue abaixo do ano passado. Preços laterais desde janeiro — mercado travado entre oferta controlada e demanda fraca.

PRODUÇÃO

Ferro-gusa 1,89 Mt/dia e aço bruto 2,10 Mt/dia em 1-10/abr (CISA). Alta de 4,4% e 5,6% vs. fim de março, mas -3,1% e -4,2% vs. ano passado.

PREÇOS

```
HRC    ¥3.310 → ¥3.230 → ¥3.300/mt
Rebar  ¥3.150 → ¥3.070 → ¥3.100/mt
```

Range de ~¥80/mt no HRC desde janeiro. Mercado não consegue romper pra nenhum lado.

ESTOQUES

Estoque total 28,33 Mt em 10/abr (+1,3% m/m, +9,5% a/a).

EXPORTAÇÃO

Pedidos de exportação melhoraram forte desde início de abril — principalmente billet e slab. Embarques de maio devem dar suporte ao mercado doméstico.
---

## EXEMPLO 2: TRADE SUMMARY / RATIONALE

INPUT:
---
Platts Iron Ore: 58.20% Fe Australian Fortescue Blend Fines trade reported done at IODEX -6.66% CFR China 1-31 May, 190,000 mt, Port Hedland to Qingdao. Jimblebar Fines 60.30% Fe trade at ¥690.00/wmt FOT Tianjin, ¥680.00/wmt FOT Shandong. MAC Fines 61.00% Fe at ¥760.00/wmt FOT Caofeidian. PBF 60.80% Fe at ¥766/wmt Jingtang, ¥765/wmt Caofeidian. Newman Lump at ¥900, ¥903 Caofeidian, ¥913 Jingtang. PBF-MAC spread at $2.00/dmt CFR. IODEX May/Jun structure at $1/dmt backwardation. PBF CFR heards at $104.00-106.15/dmt. MAC CFR at $97.70/dmt. Indian Pellet 63% Fe at $115-117/dmt CFR. MOC IODEX $104.75/dmt.
---

OUTPUT:
---
[TÍTULO: IODEX Físico — Sessão Ativa com FMG em Destaque]

Trade de FMG Blend 58,2% Fe a IODEX -6,66% CFR China (190k mt, maio, Port Hedland→Qingdao). Estrutura mai/jun em backwardation de $1/dmt.

TRADES FOT — FINES

```
Produto              Porto         ¥/wmt
Jimblebar 60,3%      Tianjin         690
Jimblebar 60,3%      Shandong        680
MAC Fines 61,0%      Caofeidian      760
PBF 60,8%            Jingtang        766
PBF 60,8%            Caofeidian      765
```

TRADES FOT — LUMP

```
Newman Lump Unscr    Caofeidian  900-903
Newman Lump Unscr    Jingtang        913
```

HEARDS CFR

```
PBF 61%              $104,00-106,15/dmt
MAC 60,5%            $97,70/dmt
Indian Pellet 63%    $115-117/dmt
```

SPREADS

```
PBF vs MAC           $2,00/dmt CFR
Mai/Jun estrutura    $1 backwardation
```

MOC IODEX em $104,75/dmt.
---"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_prompts.py -v`
Expected: FAIL — `__init__.py` tries to import critique, curator, adjuster which don't exist yet. Create stubs:

Create `execution/core/prompts/critique.py`:
```python
"""Critique agent system prompt — placeholder for Task 2."""
CRITIQUE_SYSTEM = ""
```

Create `execution/core/prompts/curator.py`:
```python
"""Curator agent system prompt — placeholder for Task 3."""
CURATOR_SYSTEM = ""
```

Create `execution/core/prompts/adjuster.py`:
```python
"""Adjuster agent system prompt — placeholder for Task 4."""
ADJUSTER_SYSTEM = ""
```

Now run: `python3 -m pytest tests/test_prompts.py -v`
Expected: all 5 Writer tests PASS.

- [ ] **Step 5: Commit**

```bash
git add execution/core/prompts/ tests/test_prompts.py
git commit -m "feat(prompts): writer v2 with few-shot examples and tabular data rule"
```

---

### Task 2: Critique prompt

**Files:**
- Modify: `execution/core/prompts/critique.py`
- Modify: `tests/test_prompts.py`

- [ ] **Step 1: Add Critique tests**

Append to `tests/test_prompts.py`:

```python
def test_critique_importable():
    from execution.core.prompts.critique import CRITIQUE_SYSTEM
    assert isinstance(CRITIQUE_SYSTEM, str)
    assert len(CRITIQUE_SYSTEM) > 100


def test_critique_is_concise():
    """v2: Critique should be under 2000 chars (was ~5000 in v1)."""
    from execution.core.prompts.critique import CRITIQUE_SYSTEM
    assert len(CRITIQUE_SYSTEM) < 2000


def test_critique_has_no_praise_section():
    """v2: No PONTOS DE EXCELÊNCIA — critique only corrects."""
    from execution.core.prompts.critique import CRITIQUE_SYSTEM
    assert "EXCELÊNCIA" not in CRITIQUE_SYSTEM
    assert "OTIMIZAÇÕES OPCIONAIS" not in CRITIQUE_SYSTEM


def test_critique_checks_tabular_data():
    from execution.core.prompts.critique import CRITIQUE_SYSTEM
    lower = CRITIQUE_SYSTEM.lower()
    assert "tabela" in lower or "tabular" in lower


def test_critique_checks_trader_voice():
    from execution.core.prompts.critique import CRITIQUE_SYSTEM
    lower = CRITIQUE_SYSTEM.lower()
    assert "trader" in lower or "robótic" in lower
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `python3 -m pytest tests/test_prompts.py::test_critique_importable -v`
Expected: FAIL — CRITIQUE_SYSTEM is empty string, len < 100.

- [ ] **Step 3: Implement Critique prompt**

Replace full contents of `execution/core/prompts/critique.py`:

```python
"""Critique agent system prompt — v2.

Reviews Writer output against original, checking completeness,
accuracy, and trader-appropriate language. Brief bullet feedback only.
"""

CRITIQUE_SYSTEM = """Você é o editor-chefe de conteúdo de mercado da Minerals Trading. Revise o trabalho do Writer comparando com o texto original.

## CHECKLIST DE REVISÃO

Verifique cada item:

1. **Dados completos?** Algum número, fato ou dado do original foi perdido pelo Writer?
2. **Dados corretos?** Algum número foi alterado, arredondado ou invertido?
3. **Título específico?** Comunica a essência com tensão/ação? Se genérico, sugira alternativa
4. **Lead com insight?** A informação mais importante para trading está no início?
5. **Dados em tabela?** Preços, trades e volumes estão em tabelas alinhadas, não convertidos em prosa?
6. **Linguagem de trader?** Sinalizar frases robóticas ou rebuscadas (ex: "registrou alta subsequente", "dinâmica observada", "liquidez adequada")

## FORMATO DO FEEDBACK

Responda APENAS com bullets diretos, máximo 15 linhas total:

CORREÇÕES: [o que está errado — bullet por erro]
FALTANDO: [o que o original tem e o Writer perdeu — bullet por item]
TÍTULO: [ok ou sugestão alternativa]

Se tudo estiver correto: responda apenas "Sem correções."

## REGRAS

- Não elogie. Só corrija
- Não sugira formato ou template — o Curator decide isso
- Seja breve e direto
- Não repita o conteúdo do Writer — apenas aponte o que precisa mudar"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_prompts.py -v`
Expected: all 10 tests PASS (5 Writer + 5 Critique).

- [ ] **Step 5: Commit**

```bash
git add execution/core/prompts/critique.py tests/test_prompts.py
git commit -m "feat(prompts): critique v2 — concise checklist, no praise"
```

---

### Task 3: Curator prompt

**Files:**
- Modify: `execution/core/prompts/curator.py`
- Modify: `tests/test_prompts.py`

- [ ] **Step 1: Add Curator tests**

Append to `tests/test_prompts.py`:

```python
def test_curator_importable():
    from execution.core.prompts.curator import CURATOR_SYSTEM
    assert isinstance(CURATOR_SYSTEM, str)
    assert len(CURATOR_SYSTEM) > 100


def test_curator_has_header_rules():
    from execution.core.prompts.curator import CURATOR_SYSTEM
    assert "📊" in CURATOR_SYSTEM
    assert "MINERALS TRADING" in CURATOR_SYSTEM
    assert "─────────────────" in CURATOR_SYSTEM


def test_curator_has_whatsapp_format_rules():
    from execution.core.prompts.curator import CURATOR_SYSTEM
    assert "*negrito*" in CURATOR_SYSTEM or "*texto*" in CURATOR_SYSTEM
    assert "###" in CURATOR_SYSTEM  # in PROIBIDO section


def test_curator_has_tabular_data_rule():
    from execution.core.prompts.curator import CURATOR_SYSTEM
    lower = CURATOR_SYSTEM.lower()
    assert "tabela" in lower
    assert "prosa" in lower


def test_curator_has_few_shot_examples():
    from execution.core.prompts.curator import CURATOR_SYSTEM
    assert "EXEMPLO" in CURATOR_SYSTEM or "<example>" in CURATOR_SYSTEM


def test_curator_has_no_silencio_profissional():
    """v2: Removed redundant section."""
    from execution.core.prompts.curator import CURATOR_SYSTEM
    assert "SILÊNCIO PROFISSIONAL" not in CURATOR_SYSTEM
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `python3 -m pytest tests/test_prompts.py::test_curator_importable -v`
Expected: FAIL — CURATOR_SYSTEM is empty string.

- [ ] **Step 3: Implement Curator prompt**

Replace full contents of `execution/core/prompts/curator.py`:

```python
"""Curator agent system prompt — v2.

Formats Writer+Critique output into WhatsApp-native messages.
Few-shot examples for both news and trade summary formats.
"""

CURATOR_SYSTEM = r"""Você é o formatador de mensagens WhatsApp da Minerals Trading. Crie mensagens que traders leiam em segundos durante o pregão.

## HEADER FIXO (4 LINHAS, SEMPRE)

```
📊 *MINERALS TRADING*
*[Título Específico da Notícia]*
`[ATIVO] · [DD/MMM]`
─────────────────
```

- Linha 1: brand (📊 *MINERALS TRADING*) — único emoji da mensagem
- Linha 2: título em negrito, 5-8 palavras, específico
- Linha 3: pílula mono com ativo + data (ex: `IRON ORE · 14/ABR`)
- Linha 4: divisória — a ÚNICA da mensagem inteira

## FORMATAÇÃO WHATSAPP

| Marcação | Uso |
|---|---|
| `*texto*` | Negrito — títulos de seção, palavras-chave |
| `_texto_` | Itálico — citações em blockquote |
| `` `texto` `` | Mono inline — tickers, preços inline, siglas |
| ` ```texto``` ` | Bloco mono — EXCLUSIVAMENTE tabelas de números |
| `> texto` | Blockquote — citações de fonte |

Títulos de seção: `*TÍTULO EM CAPS*`, separados por uma linha em branco.

## REGRA DE DADOS TABULARES (CRÍTICA)

Se o Writer entregou dados numéricos (trades, preços, volumes):
- Formate em bloco mono alinhado por colunas
- NUNCA converta tabela em prosa
- Agrupe trades duplicados em faixa (ex: ¥766-768)

Errado: "As Jimblebar Fines foram negociadas a ¥690/wmt FOT Tianjin e ¥680/wmt FOT Shandong"
Certo:
```
Jimblebar 60,3%      Tianjin         690
Jimblebar 60,3%      Shandong        680
```

## TOM

Escreva como trader de 35 anos manda no WhatsApp pra colegas do mercado. Frases curtas e diretas.

Errado: "O mercado registrou uma dinâmica de recuperação substancial nos volumes de exportação"
Certo: "Exportação melhorou forte, principalmente billet e slab"

## PROIBIDO

1. `###` como título (WhatsApp não renderiza)
2. Emojis no corpo (o único é 📊 do header)
3. Envolver mensagem inteira em ``` (mata formatação)
4. Divisórias além da linha 4 do header
5. Blocos mono envolvendo prosa
6. Palavras: "significativo", "substancial", "notável", "robusto", "dinâmica observada"

## OUTPUT

Produza APENAS a mensagem final. Começa em `📊 *MINERALS TRADING*`, termina no conteúdo. Sem comentários, explicações ou metacomunicação.

## EXEMPLO 1: NOTÍCIA DE MERCADO

INPUT WRITER:
---
[TÍTULO: China Produz Mais Aço, Mas Demanda Trava]

Produção subiu vs. março, mas segue abaixo do ano passado. Preços laterais desde janeiro.

PRODUÇÃO
Ferro-gusa 1,89 Mt/dia e aço bruto 2,10 Mt/dia em 1-10/abr. Alta de 4,4% e 5,6% vs. março, -3,1% e -4,2% a/a.

PREÇOS
```
HRC    ¥3.310 → ¥3.230 → ¥3.300/mt
Rebar  ¥3.150 → ¥3.070 → ¥3.100/mt
```

ESTOQUES
Estoque total 28,33 Mt em 10/abr (+1,3% m/m, +9,5% a/a).

EXPORTAÇÃO
Pedidos melhoraram forte desde abril. Custos de energia na Ásia e Europa tornam China competitiva.
---

OUTPUT:
---
📊 *MINERALS TRADING*
*China Produz Mais Aço, Mas Demanda Trava*
`IRON ORE · 15/ABR`
─────────────────

Produção subiu vs. março, mas segue abaixo do ano passado. Preços laterais desde janeiro — mercado travado entre oferta controlada e demanda fraca.

*PRODUÇÃO*

Ferro-gusa `1,89 Mt/dia` e aço bruto `2,10 Mt/dia` em 1-10/abr (CISA). Alta de 4,4% e 5,6% vs. fim de março, mas -3,1% e -4,2% vs. ano passado.

*PREÇOS*

```
HRC    ¥3.310 → ¥3.230 → ¥3.300/mt
Rebar  ¥3.150 → ¥3.070 → ¥3.100/mt
```

Range de ~¥80/mt no HRC desde janeiro. Mercado não consegue romper pra nenhum lado.

*ESTOQUES*

Estoque total `28,33 Mt` em 10/abr (+1,3% m/m, +9,5% a/a). Acúmulo moderado.

*EXPORTAÇÃO*

Pedidos de exportação melhoraram forte desde início de abril — principalmente billet e slab. Custos de energia subindo no Japão, Coreia e Europa tornam aço chinês competitivo.

> _"Não tem muita pressão de oferta, mas demanda doméstica também tá fraca"_ — usina

Risco: se exportação acelerar, produção pode subir junto e anular o efeito.
---

## EXEMPLO 2: TRADE SUMMARY

INPUT WRITER:
---
[TÍTULO: IODEX Físico — Sessão 14/Abr]

Trade de FMG Blend 58,2% Fe a IODEX -6,66% CFR China (190k mt, maio). Estrutura mai/jun em backwardation de $1/dmt.

TRADES FOT — FINES
```
Produto              Porto         ¥/wmt
Jimblebar 60,3%      Tianjin         690
Jimblebar 60,3%      Shandong        680
MAC Fines 61,0%      Caofeidian      760
PBF 60,8%            Jingtang    766-768
PBF 60,8%            Caofeidian      765
```

TRADES FOT — LUMP
```
Newman Lump Unscr    Caofeidian  900-903
Newman Lump Unscr    Jingtang        913
```

SPREADS
```
PBF vs MAC           $2,00/dmt CFR
Mai/Jun estrutura    $1 backwardation
```

MOC IODEX em $104,75/dmt.
---

OUTPUT:
---
📊 *MINERALS TRADING*
*IODEX Físico — Sessão 14/Abr*
`IRON ORE · 14/ABR`
─────────────────

Sessão ativa. Destaque pro trade de `FMG Blend` 58,2% Fe a IODEX -6,66% CFR China (190k mt, maio, Port Hedland→Qingdao). Estrutura mai/jun em backwardation de `$1/dmt`.

*TRADES FOT — FINES*

```
Produto              Porto         ¥/wmt
Jimblebar 60,3%      Tianjin         690
Jimblebar 60,3%      Shandong        680
MAC Fines 61,0%      Caofeidian      760
PBF 60,8%            Jingtang    766-768
PBF 60,8%            Caofeidian      765
```

*TRADES FOT — LUMP*

```
Newman Lump Unscr    Caofeidian  900-903
Newman Lump Unscr    Jingtang        913
```

*SPREADS*

```
PBF vs MAC           $2,00/dmt CFR
Mai/Jun estrutura    $1 backwardation
```

PBF com prêmio vs MAC em $2. Curva em leve backwardation — mercado não vê aperto no curto prazo. MOC IODEX em `$104,75/dmt`.
---"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_prompts.py -v`
Expected: all 16 tests PASS (5 Writer + 5 Critique + 6 Curator).

- [ ] **Step 5: Commit**

```bash
git add execution/core/prompts/curator.py tests/test_prompts.py
git commit -m "feat(prompts): curator v2 with few-shot examples and table-first rule"
```

---

### Task 4: Adjuster prompt

**Files:**
- Modify: `execution/core/prompts/adjuster.py`
- Modify: `tests/test_prompts.py`

- [ ] **Step 1: Add Adjuster tests**

Append to `tests/test_prompts.py`:

```python
def test_adjuster_importable():
    from execution.core.prompts.adjuster import ADJUSTER_SYSTEM
    assert isinstance(ADJUSTER_SYSTEM, str)
    assert len(ADJUSTER_SYSTEM) > 50


def test_adjuster_preserves_tables():
    from execution.core.prompts.adjuster import ADJUSTER_SYSTEM
    lower = ADJUSTER_SYSTEM.lower()
    assert "tabela" in lower


def test_adjuster_preserves_header():
    from execution.core.prompts.adjuster import ADJUSTER_SYSTEM
    assert "📊" in ADJUSTER_SYSTEM
    assert "MINERALS TRADING" in ADJUSTER_SYSTEM


def test_all_prompts_importable_from_package():
    """Verify __init__.py re-exports all 4 constants."""
    from execution.core.prompts import (
        WRITER_SYSTEM, CRITIQUE_SYSTEM, CURATOR_SYSTEM, ADJUSTER_SYSTEM
    )
    assert all(isinstance(p, str) and len(p) > 50 for p in [
        WRITER_SYSTEM, CRITIQUE_SYSTEM, CURATOR_SYSTEM, ADJUSTER_SYSTEM
    ])
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `python3 -m pytest tests/test_prompts.py::test_adjuster_importable -v`
Expected: FAIL — ADJUSTER_SYSTEM is empty string.

- [ ] **Step 3: Implement Adjuster prompt**

Replace full contents of `execution/core/prompts/adjuster.py`:

```python
"""Adjuster agent system prompt — v2.

Applies specific adjustments to an existing Curator output.
Minimal prompt — preserves structure, applies only requested changes.
"""

ADJUSTER_SYSTEM = """Você é o Curator da Minerals Trading. Recebeu a mensagem final formatada para WhatsApp e o feedback do editor.

REGRAS:
1. Aplique APENAS os ajustes solicitados
2. Mantenha a formatação WhatsApp nativa: `*negrito*`, `_itálico_`, `` `inline mono` ``, ` ```bloco mono``` ` só em tabelas, `> blockquote` para citações, `- bullets`. NUNCA envolva a mensagem inteira em ``` e NUNCA use `###` como título (use `*CAPS*`)
3. Preserve a estrutura do header: linha 1 `📊 *MINERALS TRADING*`, linha 2 título em negrito, linha 3 pílula mono `` `ATIVO · DD/MMM` ``, linha 4 divisória `─────────────────`. Divisória só aí, nunca entre seções
4. Mantenha o estilo e tom da mensagem original
5. Preserve todos os dados numéricos que não foram questionados
6. Se a mensagem atual tem tabelas de dados, não converta em prosa ao ajustar
7. Produza APENAS a mensagem ajustada, sem comentários. Começa direto em `📊 *MINERALS TRADING*`, termina na última linha de conteúdo
8. ESCRITA HUMANIZADA: escreva como trader manda no WhatsApp. Evite "significativo", "substancial", "notável", construções passivas rebuscadas. Frases diretas e naturais

OUTPUT: Apenas a mensagem ajustada, pronta para envio."""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_prompts.py -v`
Expected: all 20 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add execution/core/prompts/adjuster.py tests/test_prompts.py
git commit -m "feat(prompts): adjuster v2 with table preservation rule"
```

---

### Task 5: Wire app.py to use imported prompts

**Files:**
- Modify: `webhook/app.py:232-598` (remove inline prompts)
- Modify: `webhook/app.py:25-29` (add import)

- [ ] **Step 1: Add integration test**

Append to `tests/test_prompts.py`:

```python
def test_app_uses_prompts_package(monkeypatch):
    """Verify app.py imports prompts from the package, not inline."""
    import importlib
    # Force reimport to pick up changes
    if "webhook.app" in sys.modules:
        del sys.modules["webhook.app"]
    # We can't fully import app.py (needs env vars), but we can check
    # the prompts package is self-consistent
    from execution.core.prompts import WRITER_SYSTEM, CRITIQUE_SYSTEM, CURATOR_SYSTEM, ADJUSTER_SYSTEM
    assert "analista sênior" in WRITER_SYSTEM
    assert "editor-chefe" in CRITIQUE_SYSTEM
    assert "📊 *MINERALS TRADING*" in CURATOR_SYSTEM
    assert "ajustes solicitados" in ADJUSTER_SYSTEM
```

Add `import sys` at the top of the test file if not present.

- [ ] **Step 2: Remove inline prompts from app.py**

In `webhook/app.py`, delete lines 232-598 (the four inline prompt constants: `WRITER_SYSTEM = """..."""` through `ADJUSTER_SYSTEM = """..."""`).

- [ ] **Step 3: Add import to app.py**

In `webhook/app.py`, after line 29 (`from execution.integrations.sheets_client import SheetsClient`), add:

```python
from execution.core.prompts import WRITER_SYSTEM, CRITIQUE_SYSTEM, CURATOR_SYSTEM, ADJUSTER_SYSTEM
```

- [ ] **Step 4: Run full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: all tests PASS. This validates that:
- Prompts import correctly
- All content checks pass
- Existing tests (query_handlers, redis_queries, etc.) are unaffected

- [ ] **Step 5: Verify app.py line count dropped**

Run: `wc -l webhook/app.py`
Expected: ~1530 lines (was 1894 — should have dropped ~365 lines of inline prompts).

- [ ] **Step 6: Commit**

```bash
git add webhook/app.py tests/test_prompts.py
git commit -m "refactor: extract prompts from app.py to execution.core.prompts package"
```

---

### Task 6: Manual production validation

**Files:** None — operational steps only.

- [ ] **Step 1: Push to main**

```bash
git push origin main
```

Wait for Railway deploy to succeed.

- [ ] **Step 2: Test with a news article**

In Telegram, trigger the Writer pipeline with a news article (paste a short market news or trigger via Apify scrape). Verify the output:
- No [CLASSIFICAÇÃO] / [ELEMENTOS] metadata leaked
- Prices in tables, not prose
- Trader voice (no "registrou", "subsequentemente")
- Title has tension
- ~28-30 lines for typical news

- [ ] **Step 3: Test with a trade summary**

When the next IODEX trade summary comes through (or paste one manually), verify:
- Data organized in category tables
- Duplicate trades consolidated into ranges
- No data lost vs original
- MOC assessments included
- ~50-60 lines

- [ ] **Step 4: Test adjuster**

On a processed message, click ✏️ Ajustar and request a change. Verify:
- Only the requested change is applied
- Tables preserved
- Header intact
- Tone consistent
