# Agent Prompts v3 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite Writer, Critique, and Curator prompts so the 3-agent pipeline produces trader-voice WhatsApp syntheses (~18-22 lines) instead of near-verbatim translations.

**Architecture:** Replace prompt bodies in three existing files. Pipeline wiring (webhook/pipeline.py, webhook/app.py, callback_router.py) and the Adjuster prompt stay untouched. Tests in `tests/test_prompts.py` guard the new structural requirements.

**Tech Stack:** Python 3.11, pytest, Anthropic SDK (already wired in `webhook/pipeline.py`).

**Spec:** `docs/superpowers/specs/2026-04-16-agent-prompts-v3-design.md`

---

## File structure

No new files. All edits target:

| File | Change |
|------|--------|
| `execution/core/prompts/writer.py` | Full body rewrite (persona, rules, budget, drop list, bullets, 2 new examples) |
| `execution/core/prompts/critique.py` | Checklist swap (essence vs completeness), 2 new checks, anti-over-correction rule |
| `execution/core/prompts/curator.py` | 3 surgical inserts: bullets-preserve rule, hard ceiling, anti-boilerplate in PROIBIDO |
| `tests/test_prompts.py` | Add ~11 new asserts, remove 2 obsolete, update 1 |

Out of scope: `execution/core/prompts/adjuster.py`, `webhook/pipeline.py`, `webhook/app.py`, `webhook/callback_router.py`, `execution/core/prompts/__init__.py` (re-exports don't change).

---

### Task 1: Writer v3

**Files:**
- Modify: `tests/test_prompts.py` (update one test, remove one test, add six new tests)
- Modify: `execution/core/prompts/writer.py` (full body rewrite)

- [ ] **Step 1: Update `test_writer_has_inviolable_rules`**

In `tests/test_prompts.py`, replace the existing test at lines 10-16:

```python
def test_writer_has_inviolable_rules():
    from execution.core.prompts.writer import WRITER_SYSTEM
    assert "jamais arredonde" in WRITER_SYSTEM.lower() or "nunca arredonde" in WRITER_SYSTEM.lower()
    assert "nunca invente" in WRITER_SYSTEM.lower() or "não invente" in WRITER_SYSTEM.lower()
    assert "CFR" in WRITER_SYSTEM
    assert "FOB" in WRITER_SYSTEM
    assert "DATA NÃO ESPECIFICADA" in WRITER_SYSTEM
```

Change from v2: the "interpretações pessoais" assertion is replaced by "nunca invente" / "não invente" — v3 collapses that rule into the invention ban.

- [ ] **Step 2: Delete `test_writer_has_tabular_data_rule`**

In `tests/test_prompts.py`, delete the test at lines 32-35 entirely:

```python
def test_writer_has_tabular_data_rule():
    from execution.core.prompts.writer import WRITER_SYSTEM
    lower = WRITER_SYSTEM.lower()
    assert "tabela" in lower or "tabular" in lower
```

v3 Writer has no tabular rule — formatting is Curator's responsibility.

- [ ] **Step 3: Add six new Writer tests**

Append these tests to `tests/test_prompts.py` after the existing Writer tests (after the deleted `test_writer_has_tabular_data_rule`) and before `test_critique_importable`:

```python
def test_writer_has_trader_persona():
    from execution.core.prompts.writer import WRITER_SYSTEM
    lower = WRITER_SYSTEM.lower()
    assert "trader" in lower
    assert "mesa" in lower


def test_writer_has_drop_list():
    from execution.core.prompts.writer import WRITER_SYSTEM
    lower = WRITER_SYSTEM.lower()
    assert "sempre cortar" in lower or "o que cortar" in lower
    assert "platts is part of" in lower


def test_writer_has_budget():
    from execution.core.prompts.writer import WRITER_SYSTEM
    lower = WRITER_SYSTEM.lower()
    assert "1/3" in WRITER_SYSTEM or "um terço" in lower
    assert "18" in WRITER_SYSTEM and "22" in WRITER_SYSTEM


def test_writer_forbids_inventing():
    from execution.core.prompts.writer import WRITER_SYSTEM
    lower = WRITER_SYSTEM.lower()
    assert "nunca invente" in lower or "não invente" in lower


def test_writer_drops_tabular_phrase():
    from execution.core.prompts.writer import WRITER_SYSTEM
    assert "tabelas alinhadas" not in WRITER_SYSTEM


def test_writer_prefers_bullets():
    from execution.core.prompts.writer import WRITER_SYSTEM
    lower = WRITER_SYSTEM.lower()
    assert "bullet" in lower
    assert "prefira bullets" in lower or "bullets por default" in lower
```

- [ ] **Step 4: Run Writer tests — verify failures**

Run: `python3 -m pytest tests/test_prompts.py -v -k writer`

Expected: **FAIL**. The updated `test_writer_has_inviolable_rules` and all six new tests should fail because writer.py still has v2 content. `test_writer_importable`, `test_writer_has_no_classification_tags`, and `test_writer_has_few_shot_examples` should still PASS.

- [ ] **Step 5: Rewrite `execution/core/prompts/writer.py`**

Replace the full contents of `execution/core/prompts/writer.py` with:

```python
"""Writer agent system prompt — v3.

Trader persona synthesizing market news into PT-BR for WhatsApp.
Emphasizes compression over fidelity, bullets over prose,
explicit drop list to cut boilerplate.
"""

WRITER_SYSTEM = """Você é um trader sênior brasileiro da Minerals Trading, 35 anos de mesa. Você lê reports internacionais pra saber o que move o livro — não pra arquivar. Seu trabalho é escrever uma síntese em português pra mesa ler em 30 segundos no WhatsApp.

## TAREFA

- Extraia a tese + 3-5 dados que mudam decisão. Ignore o resto.
- Síntese, não tradução. O input vem em inglês, o output sai em português brasileiro.
- Se o texto não disse, você também não diz. Nunca invente dado, implicação ou citação.

## PRINCÍPIO — DESCREVER, NÃO INTERPRETAR

Descreva o fato. Só adicione leitura (1 linha curta) se o próprio texto original já conecta os pontos — nunca invente a implicação.

- Se o original diz "A aconteceu porque B", você pode dizer "A (B)".
- Se o original só diz "A aconteceu", você diz só "A".

## TAMANHO ALVO

- Output típico ≈ 1/3 das palavras do input.
- Notícia comum (~300 palavras de input): ~100-150 palavras de output, ~18-22 linhas no WhatsApp final.
- Trade dump repetitivo: consolide em faixas; output pode ficar em ~12-15 linhas.
- Rationale curto (<150 palavras de input): output curto — 6-10 linhas. Não estique pra preencher.

## BULLETS POR DEFAULT

Prefira bullets (`- ponto`) a parágrafos corridos para conteúdo descritivo também, não só dados.

- Use prosa curta apenas no **lead** (tese em 1-2 frases) e em transições inevitáveis.
- Todo o resto — contexto, trades, movimentos, citações curtas — vira bullet.
- Um fato ou ideia por bullet. Se o bullet precisar de vírgula dupla ou "e", provavelmente são dois bullets.

Ruim: "A produção subiu 4,4% em abril vs março, mas segue 3,1% abaixo do ano passado, enquanto estoques aumentaram 1,3% no mês e exportação acelerou forte desde o início de abril."

Bom:
- Produção +4,4% m/m em abril (mas -3,1% a/a)
- Estoques +1,3% m/m
- Exportação acelerou desde início de abril

## O QUE SEMPRE CORTAR

1. **Rodapé da fonte**
   Ex: "Platts is part of S&P Global Energy", "The above rationale applies to market data code <IOCLP00>", "This assessment commentary applies to the following market data codes..."

2. **Citação anônima que só repete a tese**
   Ex: se a tese é "preços subiram", corte "a trader source said prices rose today".
   Mantenha: "a trader said prices could retreat if inventories spike" — adiciona info nova.

3. **Definição de jargão**
   Ex: "CFR (Cost and Freight)...", "dry metric tonne (dmt)...". Trader já sabe.

4. **Macro genérico que não move o preço hoje**
   Ex: "amid ongoing Middle East peace talks", "against a backdrop of global uncertainty", "as market participants continue to monitor developments".

5. **Reafirmação da tese em outras palavras**
   Ex: parágrafo 1 diz "preços subiram US$ 1,90". Parágrafo 3 diz "os preços registraram alta". Corte o segundo.

6. **Fillers do original**
   Ex: "it remains to be seen...", "market participants continue to monitor...".

## REGRAS

1. **Números exatos** — se você usar um número, use o que o texto disse. Nunca arredonde.
2. **Nunca invente** — nenhum dado, implicação ou citação que não esteja no texto original.
3. **Terminologia técnica** — mantenha CFR, FOB, IODEX, Mt, dmt, etc. (não define, só usa).
4. **Data ausente** — se não há data explícita, sinalize [DATA NÃO ESPECIFICADA].

## FORMATO DE OUTPUT

[TÍTULO: título de 5-8 palavras]

[Texto em português. Lead com a tese (1-2 frases curtas).
Resto do conteúdo em bullets por default — um ponto por linha.
Seções com títulos em CAPS quando fizerem sentido.
Dados inline dentro dos bullets, no máximo 2 dados por linha.]

Não inclua tags de metadados (classificação, elementos, impacto etc.). Apenas o título e o texto.

## EXEMPLO 1: RATIONALE LONGO → SÍNTESE ENXUTA

INPUT:
---
Asian iron ore prices rose April 16, supported by liquidity surrounding mainstream iron ore materials, as Jimblebar Fines saw its first transaction since the buying curbs were lifted.

Firmer market sentiment, driven by stronger-than-expected domestic growth in China, optimism for improved steel exports following ongoing peace talks in the Middle East and potential sintering controls supporting high-grade materials, provided additional support for iron ore derivatives today, according to market sources.

Platts assessed IODEX at $107.45/dry mt on April 16, up $1.90/dmt from April 15.

BHP sold 90,000 mt of Jimblebar Fines (JMBF) basis 60.30% Fe at a discount of $5.80/dmt over the May average of 61% Fe indices, via bilateral negotiations, loading May 11-20 from Port Hedland to Qingdao. This is the first spot JMBF cargo sold by BHP since last November, which was under unofficial buying curbs last September.

Additionally, BHP sold an 80,000 mt cargo of Newman High Grade Fines (NHGF) basis 61.20% Fe at a discount of $1.89/dmt CFR China over the May average of 61% Fe indices, via bilateral negotiations, loading May 11-20 from Port Hedland to Qingdao.

Platts narrowed the Brand Adjustment for NHGF and MACF by 25 cents/dmt day over day, closing at $1.90/dmt and $2.50/dmt, respectively, on April 16.

As such, Platts adjusted the PBF Brand Adjustment to zero from minus 10 cents/dmt on the day.

Platts assessed IOPEX North China at Yuan 793/wmt FOT on April 16, up Yuan 12/wmt from April 15, or at $107.11/dmt on an import-parity basis. Platts assessed IOPEX East China at Yuan 783/wmt FOT, up Yuan 11/wmt over the same period, or at $106.33/dmt on an import-parity basis.

Platts assessed the spot lump premium at 16.70 cents/dmtu on April 16, unchanged day over day.

Platts is part of S&P Global Energy.
---

OUTPUT:
---
[TÍTULO: IODEX Sobe $1,90 com Volta da Jimblebar]

IODEX em $107,45/dmt CFR North China (+$1,90/dmt d/d). Primeira carga spot de JMBF desde as curbs informais marca retomada de liquidez em brand BHP.

TRADES BHP (ambos mai, Port Hedland→Qingdao)
- JMBF 60,3% Fe: 90k mt a IODEX -$5,80/dmt
- NHGF 61,2% Fe: 80k mt a IODEX -$1,89/dmt

BRAND ADJUSTMENT
- NHGF: -25¢/dmt d/d, fechou $1,90/dmt
- MACF: -25¢/dmt d/d, fechou $2,50/dmt
- PBF: zerado (de -$0,10/dmt) — efeito da queda das curbs em BHP

PORT-STOCK
- IOPEX North: ¥793/wmt FOT (+¥12 d/d, $107,11/dmt import-parity)
- IOPEX East: ¥783/wmt FOT (+¥11 d/d, $106,33/dmt import-parity)

LUMP
- Spot premium: 16,70¢/dmtu (inalterado)
---

## EXEMPLO 2: TRADE DUMP REPETITIVO → CONSOLIDAÇÃO EM FAIXAS

INPUT:
---
Platts Iron Ore: 61.60% Fe Australian Pilbara Blend Lump tradeable value heard from International Trader source at IODEX +$0.16/dmtu CFR China 30 Apr - 11 Jun pricing on 16 Apr, from Dampier delivery 30 Apr - 11 Jun to Qingdao.

Platts Iron Ore: 62.00% Fe Australian Newman Blend Lump tradeable value heard from Chinese Trader source at IODEX +$0.16/dmtu CFR China 30 Apr - 11 Jun pricing on 16 Apr, from Port Hedland delivery 30 Apr - 11 Jun to Qingdao.

Platts Iron Ore: 61.60% Fe Australian Pilbara Blend Lump tradeable value heard from International Trader source at IODEX +$0.1670/dmtu CFR China 30 Apr - 11 Jun pricing on 16 Apr, from Dampier delivery 30 Apr - 11 Jun to Qingdao.

Platts Iron Ore: 62.00% Fe Australian Newman Blend Lump tradeable value heard from International Trader source at IODEX +$0.1670/dmtu CFR China 30 Apr - 11 Jun pricing on 16 Apr, from Port Hedland delivery 30 Apr - 11 Jun to Qingdao.

Platts Iron Ore: 61.60% Fe Australian Pilbara Blend Lump tradeable value heard from Chinese Trader source at IODEX +$0.1670/dmtu CFR China 30 Apr - 11 Jun pricing on 16 Apr, from Dampier delivery 30 Apr - 11 Jun to Qingdao.

Platts Iron Ore: 62.00% Fe Australian Newman Blend Lump tradeable value heard from Chinese Trader source at IODEX +$0.1670/dmtu CFR China 30 Apr - 11 Jun pricing on 16 Apr, from Port Hedland delivery 30 Apr - 11 Jun to Qingdao.

Platts Iron Ore: 61.60% Fe Australian Pilbara Blend Lump tradeable value heard from International Trader source at IODEX +$0.14 to 0.15/dmtu CFR China 30 Apr - 11 Jun pricing on 16 Apr, from Dampier delivery 30 Apr - 11 Jun to Qingdao.

Platts Iron Ore: 62.00% Fe Australian Newman Blend Lump tradeable value heard from International Trader source at IODEX +$0.14 to 0.15/dmtu CFR China 30 Apr - 11 Jun pricing on 16 Apr, from Port Hedland delivery 30 Apr - 11 Jun to Qingdao.

Platts Iron Ore: 61.60% Fe Australian Pilbara Blend Lump tradeable value heard from Chinese Trader source at IODEX +$0.1650/dmtu CFR China 30 Apr - 11 Jun pricing on 16 Apr, from Dampier delivery 30 Apr - 11 Jun to Qingdao.

Platts Iron Ore: 61.60% Fe Australian Pilbara Blend Lump tradeable value heard from Chinese Steel Mill source at IODEX +$0.1550/dmtu CFR China 30 Apr - 11 Jun pricing on 16 Apr, from Dampier delivery 30 Apr - 11 Jun to Qingdao.

Platts Iron Ore: 62.00% Fe Australian Newman Blend Lump tradeable value heard from Chinese Steel Mill source at IODEX +$0.1550/dmtu CFR China 30 Apr - 11 Jun pricing on 16 Apr, from Port Hedland delivery 30 Apr - 11 Jun to Qingdao.

This assessment commentary applies to the following market data codes:<IOCLP00>
---

OUTPUT:
---
[TÍTULO: PBL/NBL Lump — Heards em $0,14-0,17/dmtu]

Heards de tradeable pra lump blend australiano — entrega e pricing 30/abr-11/jun, Qingdao CFR. Range geral dos níveis: $0,14-0,17/dmtu sobre IODEX.

PBL 61,6% Fe (Dampier)
- Trader Int'l: IODEX +$0,14-0,15 · +$0,16 · +$0,1670/dmtu
- Trader CN: IODEX +$0,16 · +$0,1650 · +$0,1670/dmtu
- Steel Mill CN: IODEX +$0,1550/dmtu

NBL 62,0% Fe (Port Hedland)
- Trader Int'l: IODEX +$0,14-0,15 · +$0,16 · +$0,1670/dmtu
- Trader CN: IODEX +$0,16 · +$0,1670/dmtu
- Steel Mill CN: IODEX +$0,1550/dmtu

Steel mills no piso da faixa, traders no topo.
---"""
```

- [ ] **Step 6: Run Writer tests — verify pass**

Run: `python3 -m pytest tests/test_prompts.py -v -k writer`

Expected: **PASS** for all Writer tests:
- `test_writer_importable`
- `test_writer_has_inviolable_rules`
- `test_writer_has_no_classification_tags`
- `test_writer_has_few_shot_examples`
- `test_writer_has_trader_persona`
- `test_writer_has_drop_list`
- `test_writer_has_budget`
- `test_writer_forbids_inventing`
- `test_writer_drops_tabular_phrase`
- `test_writer_prefers_bullets`

- [ ] **Step 7: Commit**

```bash
git add execution/core/prompts/writer.py tests/test_prompts.py
git commit -m "feat(prompts): writer v3 — trader persona, drop list, bullets, budget"
```

---

### Task 2: Critique v3

**Files:**
- Modify: `tests/test_prompts.py` (remove one test, add three new tests)
- Modify: `execution/core/prompts/critique.py` (partial rewrite)

- [ ] **Step 1: Delete `test_critique_checks_tabular_data`**

In `tests/test_prompts.py`, delete this test entirely (was at lines 57-60 before Task 1 edits):

```python
def test_critique_checks_tabular_data():
    from execution.core.prompts.critique import CRITIQUE_SYSTEM
    lower = CRITIQUE_SYSTEM.lower()
    assert "tabela" in lower or "tabular" in lower
```

v3 Critique does not check tabular formatting — that's Curator's job.

- [ ] **Step 2: Add three new Critique tests**

Append these tests to `tests/test_prompts.py` after the existing Critique tests (after `test_critique_checks_trader_voice`) and before `test_curator_importable`:

```python
def test_critique_checks_essence_not_completeness():
    from execution.core.prompts.critique import CRITIQUE_SYSTEM
    lower = CRITIQUE_SYSTEM.lower()
    assert "essência" in lower or "tese" in lower
    assert "dados completos" not in lower


def test_critique_checks_bloat():
    from execution.core.prompts.critique import CRITIQUE_SYSTEM
    lower = CRITIQUE_SYSTEM.lower()
    assert "inchado" in lower or "boilerplate" in lower or "repetição" in lower


def test_critique_checks_invention():
    from execution.core.prompts.critique import CRITIQUE_SYSTEM
    lower = CRITIQUE_SYSTEM.lower()
    assert "invenção" in lower or "invent" in lower
```

- [ ] **Step 3: Run Critique tests — verify failures**

Run: `python3 -m pytest tests/test_prompts.py -v -k critique`

Expected: **FAIL** for the three new tests (`test_critique_checks_essence_not_completeness`, `test_critique_checks_bloat`, `test_critique_checks_invention`). Existing `test_critique_importable`, `test_critique_is_concise`, `test_critique_has_no_praise_section`, and `test_critique_checks_trader_voice` should still PASS.

- [ ] **Step 4: Rewrite `execution/core/prompts/critique.py`**

Replace the full contents of `execution/core/prompts/critique.py` with:

```python
"""Critique agent system prompt — v3.

Reviews Writer output for essence preservation (not completeness),
bloat, and invention. Anti-over-correction: compression of
low-signal content is expected, not an error.
"""

CRITIQUE_SYSTEM = """Você é o editor-chefe de conteúdo de mercado da Minerals Trading. Revise o trabalho do Writer comparando com o texto original.

## CHECKLIST DE REVISÃO

Verifique cada item:

1. **Essência preservada?** Tese + números que movem decisão estão no output? (Não é "dados completos" — é "dados que importam".)
2. **Números exatos?** Algum número foi alterado, arredondado ou invertido?
3. **Título específico?** Comunica a essência com tensão/ação? Se genérico, sugira alternativa.
4. **Lead com tese?** A informação mais importante para trading está no início?
5. **Voz de trader?** Sinalizar frases robóticas ou rebuscadas (ex: "registrou alta subsequente", "dinâmica observada", "liquidez adequada").
6. **Inchado?** Há boilerplate (rodapé Platts, "applies to market data code"), repetição, citação anônima que só repete a tese, ou macro genérico?
7. **Invenção?** O Writer adicionou implicação, número ou citação que não está no texto original?

## REGRA ANTI-OVER-CORRECTION

Se o Writer cortou coisa que não move decisão, **não reclame** — era pra cortar mesmo. Só sinalize FALTANDO se o que saiu fora é a tese, número-chave ou dado acionável.

## FORMATO DO FEEDBACK

Responda APENAS com bullets diretos, máximo 15 linhas total:

CORREÇÕES: [erros de número, título genérico, invenção]
FALTANDO: [só se for tese ou número-chave que saiu]
INCHADO: [boilerplate, repetição, citação filler que passou]
TÍTULO: [ok ou sugestão alternativa]

Se tudo estiver correto: responda apenas "Sem correções."

## REGRAS

- Não elogie. Só corrija.
- Não sugira formato ou template — o Curator decide isso.
- Seja breve e direto.
- Não repita o conteúdo do Writer — apenas aponte o que precisa mudar."""
```

- [ ] **Step 5: Run Critique tests — verify pass**

Run: `python3 -m pytest tests/test_prompts.py -v -k critique`

Expected: **PASS** for all Critique tests:
- `test_critique_importable`
- `test_critique_is_concise`
- `test_critique_has_no_praise_section`
- `test_critique_checks_trader_voice`
- `test_critique_checks_essence_not_completeness`
- `test_critique_checks_bloat`
- `test_critique_checks_invention`

Note: `test_critique_is_concise` requires `len(CRITIQUE_SYSTEM) < 2000`. The v3 body is ~1400 chars — safely under the cap.

- [ ] **Step 6: Commit**

```bash
git add execution/core/prompts/critique.py tests/test_prompts.py
git commit -m "feat(prompts): critique v3 — essence, bloat, invention checks"
```

---

### Task 3: Curator v3

**Files:**
- Modify: `tests/test_prompts.py` (add two new tests)
- Modify: `execution/core/prompts/curator.py` (three surgical edits)

- [ ] **Step 1: Add two new Curator tests**

Append these tests to `tests/test_prompts.py` after the existing Curator tests (after `test_curator_has_no_silencio_profissional`) and before `test_adjuster_importable`:

```python
def test_curator_has_hard_ceiling():
    from execution.core.prompts.curator import CURATOR_SYSTEM
    assert "TETO DURO" in CURATOR_SYSTEM
    assert "25 linhas" in CURATOR_SYSTEM


def test_curator_removes_source_footer():
    from execution.core.prompts.curator import CURATOR_SYSTEM
    lower = CURATOR_SYSTEM.lower()
    assert "platts is part of" in lower
```

- [ ] **Step 2: Run Curator tests — verify failures**

Run: `python3 -m pytest tests/test_prompts.py -v -k curator`

Expected: **FAIL** for the two new tests. All existing Curator tests should still PASS.

- [ ] **Step 3: Edit curator.py — insert BULLETS section**

In `execution/core/prompts/curator.py`, use Edit to replace this block:

**OLD:**
```
Títulos de seção: `*TÍTULO EM CAPS*`, separados por uma linha em branco.

## REGRA DE DADOS TABULARES (CRÍTICA)
```

**NEW:**
```
Títulos de seção: `*TÍTULO EM CAPS*`, separados por uma linha em branco.

## BULLETS DO WRITER

Se o Writer entregou bullets (`- texto`), preserve como bullets no WhatsApp. Não converta bullets de volta em parágrafo corrido. WhatsApp renderiza `- item` como bullet nativo.

## REGRA DE DADOS TABULARES (CRÍTICA)
```

- [ ] **Step 4: Edit curator.py — insert TETO DURO section**

In `execution/core/prompts/curator.py`, use Edit to replace this block:

**OLD:**
```
PBF 60,8% — `¥765-768` Jingtang/Caofeidian

## TOM
```

**NEW:**
```
PBF 60,8% — `¥765-768` Jingtang/Caofeidian

## TETO DURO

Mensagem inteira (header + corpo) ≤ ~25 linhas visíveis no celular. Se o Writer entregou algo que passa disso, corte nesta ordem:

1. Primeiro: seção que menos move decisão do trader
2. Depois: citações (blockquotes) se a mensagem ainda tiver sobra
3. Nunca corte: header, tese (lead), dados numéricos principais

## TOM
```

- [ ] **Step 5: Edit curator.py — add anti-boilerplate to PROIBIDO**

In `execution/core/prompts/curator.py`, use Edit to replace this block:

**OLD:**
```
6. Palavras: "significativo", "substancial", "notável", "robusto", "dinâmica observada"

## OUTPUT
```

**NEW:**
```
6. Palavras: "significativo", "substancial", "notável", "robusto", "dinâmica observada"
7. Rodapé de fonte que o Writer deixou passar ("Platts is part of S&P Global", "The above rationale applies to market data code...") — remova antes de formatar.

## OUTPUT
```

- [ ] **Step 6: Run Curator tests — verify pass**

Run: `python3 -m pytest tests/test_prompts.py -v -k curator`

Expected: **PASS** for all Curator tests:
- `test_curator_importable`
- `test_curator_has_header_rules`
- `test_curator_has_whatsapp_format_rules`
- `test_curator_has_tabular_data_rule`
- `test_curator_has_few_shot_examples`
- `test_curator_has_no_silencio_profissional`
- `test_curator_has_hard_ceiling`
- `test_curator_removes_source_footer`

- [ ] **Step 7: Commit**

```bash
git add execution/core/prompts/curator.py tests/test_prompts.py
git commit -m "feat(prompts): curator v3 — bullets preserve, hard ceiling, anti-boilerplate"
```

---

### Task 4: Full suite verification + manual production validation

**Files:** None (no code changes).

- [ ] **Step 1: Run full test suite**

Run: `python3 -m pytest tests/test_prompts.py -v`

Expected: **PASS** for every test in the file (20 Writer + Critique + Curator + Adjuster tests). Count should be exactly:
- 4 Writer v2 tests kept (`test_writer_importable`, `test_writer_has_inviolable_rules` updated, `test_writer_has_no_classification_tags`, `test_writer_has_few_shot_examples`)
- 6 Writer v3 tests new
- 4 Critique tests kept (importable, is_concise, has_no_praise, checks_trader_voice)
- 3 Critique v3 tests new
- 6 Curator tests kept
- 2 Curator v3 tests new
- 3 Adjuster tests kept
- 1 package-level test kept

Total ≈ 29 tests. If any Adjuster or package test fails, that's a regression — investigate before proceeding.

- [ ] **Step 2: Run full repo tests — no regressions elsewhere**

Run: `python3 -m pytest tests/ -v`

Expected: **PASS** for every test in the repo. If unrelated tests fail, the prompts changes broke something unexpected (e.g., `test_app_uses_prompts_package`). Do not proceed until green.

- [ ] **Step 3: Pull 5 Redis samples for manual validation**

Make sure `.env` has `REDIS_URL` set. Run this script to save 5 real samples to local JSON:

```bash
set -a; source .env; set +a
python3 - <<'PY'
import json
from webhook.redis_queries import list_archive_recent, list_staging

samples = []
seen_types = set()
# Target: long rationale, medium rationale, short rationale, trade dump, news
pool = list_archive_recent(limit=20) + list_staging(limit=20)
for it in pool:
    ft = it.get('fullText') or ''
    if len(ft) < 300:
        # short rationale
        bucket = 'short'
    elif len(ft) < 1800:
        bucket = 'medium'
    elif it.get('type') == 'news':
        bucket = 'news'
    elif 'trade' in (it.get('title') or '').lower() or 'bids, offers' in (it.get('title') or '').lower():
        bucket = 'trade_dump'
    else:
        bucket = 'long_rationale'
    if bucket in seen_types:
        continue
    seen_types.add(bucket)
    samples.append({
        'bucket': bucket,
        'id': it.get('id'),
        'title': it.get('title'),
        'chars': len(ft),
        'words': len(ft.split()),
        'raw_text': (
            f"Title: {it.get('title', '')}\n"
            f"Date: {it.get('publishDate', '')}\n"
            f"Source: {it.get('source', '')}\n\n"
            f"{ft}"
        ),
    })
    if len(samples) >= 5:
        break

with open('/tmp/v3_samples.json', 'w') as f:
    json.dump(samples, f, ensure_ascii=False, indent=2)

print(f"Saved {len(samples)} samples to /tmp/v3_samples.json")
for s in samples:
    print(f"  - {s['bucket']}: {s['title'][:60]} ({s['chars']} chars)")
PY
```

Expected: 5 samples saved. If fewer than 5 buckets are filled, it's OK — proceed with what you have, but note the gap.

- [ ] **Step 4: Run the v3 pipeline against each sample**

Requires `ANTHROPIC_API_KEY` in `.env`. Run:

```bash
set -a; source .env; set +a
python3 - <<'PY'
import json
from webhook.pipeline import run_3_agents

with open('/tmp/v3_samples.json') as f:
    samples = json.load(f)

results = []
for s in samples:
    print(f"\n{'='*60}\n{s['bucket'].upper()}: {s['title']}\n{'='*60}")
    out = run_3_agents(s['raw_text'])
    lines = out.split('\n')
    line_count = sum(1 for l in lines if l.strip())
    print(out)
    print(f"\n--- METRICS: {line_count} non-empty lines, {len(out)} chars ---")
    results.append({
        'bucket': s['bucket'],
        'id': s['id'],
        'title': s['title'],
        'input_chars': s['chars'],
        'output_chars': len(out),
        'output_lines': line_count,
        'output': out,
    })

with open('/tmp/v3_outputs.json', 'w') as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(f"\nSaved {len(results)} outputs to /tmp/v3_outputs.json")
PY
```

Expected: each sample produces a formatted WhatsApp message. This costs Claude API tokens — budget roughly 5 × ~8k tokens ≈ 40k tokens total (~$0.50 on sonnet-4-6).

- [ ] **Step 5: Manual check against acceptance criteria**

Open `/tmp/v3_outputs.json` and verify for each output:

| Check | Criterion |
|-------|-----------|
| Line count | ≤ 25 non-empty lines (hard ceiling). News/rationale samples ideally 18-22. Trade dump can be ~12-15. Short rationale ~6-10. |
| No boilerplate | Search output for: `"Platts is part of"`, `"applies to market data code"`, `"S&P Global Energy"`. None should appear. |
| Numbers exact | Pick 3 numbers per output and grep them in the original `raw_text`. They must match digit-for-digit (e.g., if output says `$107,45`, original must say `$107.45` — BR comma decimal is the only allowed transform). |
| No invention | Read output and ask: is there any claim (price, trade, implication, source quote) that is NOT in the original? If yes, Writer invented. |
| Bullets used | Output body should be dominated by `- ` bullets, not paragraphs of prose. Lead can be prose. |
| Header intact | First 4 lines: `📊 *MINERALS TRADING*`, title bold, asset/date pill in mono, divider. |

Document findings in the PR description as a table with one row per sample.

- [ ] **Step 6: Decision gate**

If all 5 samples pass the criteria: proceed to push.
If 1+ samples fail any criterion: do NOT push. Diagnose:

- **Too long?** The ceiling isn't being enforced. Tighten Curator's TETO DURO wording or reduce the line count.
- **Boilerplate leaking?** Add the exact leaked phrase to Writer's drop list or Curator's PROIBIDO item 7.
- **Invention?** The "nunca invente" rule isn't strong enough — consider moving it to the top of Writer's REGRAS.
- **Too short / essence lost?** The drop list was over-applied. Loosen it or strengthen Critique's essence check.

After any fix, re-run Step 4 to re-validate. Do not skip re-validation.

- [ ] **Step 7: Push and watch production**

```bash
git push origin main
```

Wait for Railway deploy to succeed. Then trigger the pipeline on 2-3 fresh news items via the Telegram bot and confirm:

- Messages arrive within ~25 lines
- No "Platts is part of" / "applies to market data code" leaks
- Numbers match the originals archived in Redis
- Reads like a trader-WhatsApp synthesis, not a translation

If any of these fail in production, revert via `git revert <commit>` and re-push. No DB migration or state rollback needed.

---

## Rollback

If a post-merge issue is detected:

```bash
git log --oneline | head -5   # find the 3 v3 commits
git revert <writer-sha> <critique-sha> <curator-sha> --no-edit
git push origin main
```

Since the change is prompt-only (no schema, no state, no feature flag), revert + deploy is the full rollback.
