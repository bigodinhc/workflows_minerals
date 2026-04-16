# Agent Prompts v3 — Design Doc

**Topic:** Rewrite Writer, Critique, and Curator prompts to produce true synthesis (trader-voice) instead of near-verbatim translation.

**Date:** 2026-04-16

**Status:** Approved — ready for plan.

---

## Problem

In production the pipeline emits WhatsApp messages that feel like translations of the original Platts/S&P text, not syntheses. Messages are too long and read like "copy + translate to PT-BR" rather than "trader wrote a summary for the desk."

Root causes identified in v2 prompts:

1. **Writer pushes fidelity, not compression.** Rules like *"Dados numéricos preservados com precisão absoluta"* and *"Relate apenas o que o texto diz"* frame the task as translation. No length budget exists.
2. **Critique punishes compression.** Checklist item 1 asks *"Algum número, fato ou dado do original foi perdido?"* — directly rewards preserving everything.
3. **No drop list.** No prompt enumerates concrete things to cut (boilerplate, anonymous-source filler, term definitions, macro padding).
4. **Writer's Example 2 models columnar tables** (6-row grid), contradicting the text rule that requires compact inline format. The example wins: model copies it.
5. **Curator has no ceiling.** Even if the Writer bloats, the final stage doesn't trim.

Measured input universe from Redis (`platts:staging:*` / `platts:archive:*`): news ranges from ~119 to ~683 words. The same pipeline handles all of it with one uniform set of rules.

---

## Goal

Rewrite prompts so a typical news item (~200-400 words of input) produces an 18-22 line WhatsApp message that reads like a senior trader's WhatsApp summary: thesis first, 3-5 decision-moving numbers, everything else cut.

**Non-goal:** changing the pipeline architecture, adding new stages, or reformatting the WhatsApp visual layout. Those are stable and out of scope.

---

## Architecture

No structural changes. Pipeline stays:

```
raw_text (title + date + source + fullText, in English)
  → Writer (PT-BR synthesis)
  → Critique (feedback bullets)
  → Curator (WhatsApp formatting)
  → final message sent to Telegram
```

Adjuster (separate, post-send feedback) is not touched in this spec.

Files affected:
- `execution/core/prompts/writer.py` — rewrite body
- `execution/core/prompts/critique.py` — partial rewrite
- `execution/core/prompts/curator.py` — add ceiling + anti-boilerplate rule
- `tests/test_prompts.py` — add v3 assertions; update/remove v2-specific assertions

Out of scope: `execution/core/prompts/adjuster.py` (unchanged in this cycle).

---

## Writer v3

### Persona

From *"analista sênior de mercado de minério de ferro"* → to:

> **Você é um trader sênior brasileiro da Minerals Trading, 35 anos de mesa. Você lê reports internacionais pra saber o que move o livro — não pra arquivar. Seu trabalho é escrever uma síntese em português pra mesa ler em 30 segundos no WhatsApp.**

### Task reframe

Replace current "TAREFA" section. New language:

- "Extraia a tese + 3-5 dados que mudam decisão. Ignore o resto."
- "Síntese, não tradução. O input é em inglês, o output em português brasileiro."
- "Se o texto não disse, você também não diz. Nunca invente dado, implicação ou citação."

### Opção 1 com toque de 2

Single new rule:

> **Descreva o fato.** Só adicione leitura (1 linha curta) se o próprio texto original já conecta os pontos — nunca invente a implicação. Se o original diz "A aconteceu porque B", você pode dizer "A (B)". Se o original só diz "A aconteceu", você diz só "A".

### Budget explícito

New section:

> **TAMANHO ALVO**
>
> - Output típico ≈ 1/3 das palavras do input.
> - Notícia comum (~300 palavras de input): ~100-150 palavras de output, ~18-22 linhas no WhatsApp final.
> - Trade dump repetitivo: consolide em faixas; output pode ficar em ~12-15 linhas.
> - Rationale curto (<150 palavras de input): output curto — 6-10 linhas. Não estique pra preencher.

### Drop list

New section — explicit, with examples:

> **O QUE SEMPRE CORTAR**
>
> 1. **Rodapé da fonte**
>    Ex: *"Platts is part of S&P Global Energy"*, *"The above rationale applies to market data code <IOCLP00>"*, *"This assessment commentary applies to the following market data codes..."*
>
> 2. **Citação anônima que só repete a tese**
>    Ex: se a tese é "preços subiram", corte *"a trader source said prices rose today"*.
>    Mantenha: *"a trader said prices could retreat if inventories spike"* — adiciona info nova.
>
> 3. **Definição de jargão**
>    Ex: *"CFR (Cost and Freight)..."*, *"dry metric tonne (dmt)..."*. Trader já sabe.
>
> 4. **Macro genérico que não move o preço hoje**
>    Ex: *"amid ongoing Middle East peace talks"*, *"against a backdrop of global uncertainty"*, *"as market participants continue to monitor developments"*.
>
> 5. **Reafirmação da tese em outras palavras**
>    Ex: parágrafo 1 diz "preços subiram US$ 1,90". Parágrafo 3 diz "os preços registraram alta". Corte o segundo.
>
> 6. **Fillers do original**
>    Ex: *"it remains to be seen..."*, *"market participants continue to monitor..."*.

### Rules renamed / trimmed

Current "REGRAS INEGOCIÁVEIS" becomes "REGRAS". Cleaner, shorter:

1. **Números exatos** — se você usar um número, use o que o texto disse. Nunca arredonde.
2. **Nunca invente** — nenhum dado, implicação ou citação que não esteja no texto original.
3. **Terminologia técnica** — mantenha CFR, FOB, IODEX, Mt, dmt, etc. (não define, só usa).
4. **Data ausente** — se não há data explícita, sinalize `[DATA NÃO ESPECIFICADA]`.

Drop:
- Item 5 atual ("Distinção clara entre fatos e especulações") — coberto pelo item 2 novo.

### Output format fix

Current FORMATO DE OUTPUT says *"dados em tabelas alinhadas"*. This contradicts the compact-inline rule. Remove that phrase. New format description:

```
[TÍTULO: título de 5-8 palavras]

[Texto em português. Lead com a tese (1-2 frases curtas).
Resto do conteúdo em bullets por default — um ponto por linha.
Seções com títulos em CAPS quando fizerem sentido.
Dados inline dentro dos bullets, no máximo 2 dados por linha.]
```

Remove all references to tabular/columnar layout in the Writer prompt body. Curator decides visual format.

### Bullets por default

New rule section:

> **BULLETS POR DEFAULT**
>
> Prefira bullets (`- ponto`) a parágrafos corridos para conteúdo descritivo também, não só dados.
> - Use prosa curta apenas no **lead** (tese em 1-2 frases) e em transições inevitáveis.
> - Todo o resto — contexto, trades, movimentos, citações curtas — vira bullet.
> - Um fato ou ideia por bullet. Se o bullet precisar de vírgula dupla ou "e", provavelmente são dois bullets.
>
> Ruim: *"A produção subiu 4,4% em abril vs março, mas segue 3,1% abaixo do ano passado, enquanto estoques aumentaram 1,3% no mês e exportação acelerou forte desde o início de abril."*
>
> Bom:
> - *Produção +4,4% m/m em abril (mas -3,1% a/a)*
> - *Estoques +1,3% m/m*
> - *Exportação acelerou desde início de abril*

### Exemplos

**Replace both examples.** New examples are calibrated with real Redis inputs:

- **Exemplo 1: Rationale longo** — based on `"Asian iron ore prices rise as Jimblebar returns, robust sentiments"` (4.376 chars input). Show compression to ~18 lines. Must demonstrate: thesis in lead, key trades preserved, macro ("peace talks") dropped, boilerplate dropped, consolidation of brand adjustment moves.
- **Exemplo 2: Trade dump** — based on `"Platts China Iron Ore Lump Premium Bids, Offers, Trades"` (2.620 chars input with 12 repetitive "heard" entries). Show consolidation: 12 entries → 3-4 lines using price ranges (e.g., `$0.14-0.17/dmtu` from different sources).

Exemplos completos são escritos durante a implementação (Task 1 do plan), usando o conteúdo real já coletado do Redis.

---

## Critique v3

### Checklist changes

Current v2 items and v3 replacements:

| v2 item | v3 replacement |
|---|---|
| 1. Dados completos? Algum número, fato ou dado do original foi perdido? | **1. Essência preservada?** Tese + números que movem decisão estão no output? |
| 2. Dados corretos? Algum número foi alterado, arredondado ou invertido? | **2. Números exatos?** (inalterado) |
| 3. Título específico? | **3. Título específico?** (inalterado) |
| 4. Lead com insight? | **4. Lead com tese?** (renomeado, mesmo teste) |
| 5. Dados em tabela? | **REMOVED** — formato é decisão do Curator |
| 6. Linguagem de trader? | **5. Voz de trader?** (renomeado) |
| — | **6. NOVO — Inchado?** Há boilerplate, repetição, citação anônima que só repete a tese, ou macro genérico? |
| — | **7. NOVO — Invenção?** O Writer adicionou implicação, número ou citação que não está no original? |

### Anti-over-correction rule (nova)

New line in the REGRAS section:

> Se o Writer cortou coisa que não move decisão, **não reclame** — era pra cortar mesmo. Só sinalize FALTANDO se o que saiu fora é a tese, número-chave ou dado acionável.

### Size cap

Current: *"máximo 15 linhas total"* — keep, mas o formato se adapta aos novos items:

```
CORREÇÕES: [erros de número, título genérico, invenção]
FALTANDO: [só se for tese ou número-chave que saiu]
INCHADO: [boilerplate, repetição, citação filler que passou]
TÍTULO: [ok ou sugestão]
```

### Feedback neutro

Current rule *"Se tudo estiver correto: responda apenas 'Sem correções.'"* — mantido.

---

## Curator v3

### O que muda

Mantém ~95% do prompt v2. Adiciona:

**0. Preservar bullets do Writer:**

> Se o Writer entregou bullets (`- texto`), preserve como bullets no WhatsApp. Não converta bullets de volta em parágrafo corrido. WhatsApp renderiza `- item` como bullet nativo.

**1. Teto duro de linhas (nova seção curta, depois da regra tabular):**

> **TETO DURO**
>
> Mensagem inteira (header + corpo) ≤ ~25 linhas visíveis no celular. Se o Writer entregou algo que passa disso, corte:
> 1. Primeiro: seção que menos move decisão do trader
> 2. Depois: citações (blockquotes) se a mensagem ainda tiver sobra
> 3. Nunca corte: header, tese (lead), dados numéricos principais

**2. Rede de segurança anti-boilerplate (uma linha na seção PROIBIDO):**

> 7. Rodapé de fonte que o Writer deixou passar (`"Platts is part of S&P Global"`, `"The above rationale applies to market data code..."`) — remova antes de formatar.

### O que fica igual

- Header de 4 linhas
- Regra de formatação WhatsApp (negrito, itálico, mono inline, mono bloco, blockquote)
- Regra de dados tabulares (inline mono + agrupamento em faixas)
- Regra de tom (trader de 35 anos, sem "significativo", "substancial", etc.)
- Lista PROIBIDO (+ o item novo acima)
- Exemplos de output — os 2 exemplos atuais continuam válidos pra formato WhatsApp, mas os inputs de exemplo devem ser atualizados pra refletir os outputs do Writer v3 (mais curtos).

---

## Tests

### `tests/test_prompts.py` — additions

Writer v3:
```python
def test_writer_has_trader_persona():
    assert "trader" in WRITER_SYSTEM.lower()
    assert "mesa" in WRITER_SYSTEM.lower()

def test_writer_has_drop_list():
    lower = WRITER_SYSTEM.lower()
    assert "sempre cortar" in lower or "o que cortar" in lower or "drop" in lower
    assert "platts is part of s&p" in lower or "platts is part of" in lower

def test_writer_has_budget():
    lower = WRITER_SYSTEM.lower()
    assert "1/3" in WRITER_SYSTEM or "um terço" in lower
    assert "18" in WRITER_SYSTEM and "22" in WRITER_SYSTEM  # line range

def test_writer_forbids_inventing():
    lower = WRITER_SYSTEM.lower()
    assert "nunca invente" in lower or "não invente" in lower

def test_writer_drops_tabular_phrase():
    # v3: tabular layout is Curator's job — Writer prompt must not say
    # "tabelas alinhadas" in the output format description.
    # Still allowed: "dados compactos" or similar compact-inline wording.
    assert "tabelas alinhadas" not in WRITER_SYSTEM

def test_writer_prefers_bullets():
    lower = WRITER_SYSTEM.lower()
    assert "bullet" in lower
    assert "prefira bullets" in lower or "bullets por default" in lower
```

Critique v3:
```python
def test_critique_checks_essence_not_completeness():
    # v2 had "dados completos". v3 should ask about essence, not completeness.
    lower = CRITIQUE_SYSTEM.lower()
    assert "essência" in lower or "tese" in lower
    assert "dados completos" not in lower

def test_critique_checks_bloat():
    lower = CRITIQUE_SYSTEM.lower()
    assert "inchado" in lower or "boilerplate" in lower or "repetição" in lower

def test_critique_checks_invention():
    lower = CRITIQUE_SYSTEM.lower()
    assert "invenção" in lower or "invent" in lower
```

Curator v3:
```python
def test_curator_has_hard_ceiling():
    lower = CURATOR_SYSTEM.lower()
    assert "teto" in lower or "25 linhas" in CURATOR_SYSTEM or "25" in CURATOR_SYSTEM

def test_curator_removes_source_footer():
    lower = CURATOR_SYSTEM.lower()
    assert "platts is part of" in lower or "rodapé" in lower
```

### Tests to update or remove

Current v2 asserts that need adjustment:

- `test_critique_checks_tabular_data` — **remove** (v3 Critique doesn't check tabular).
- `test_writer_has_tabular_data_rule` — **remove** (v3 Writer has no tabular rule; replaced by `test_writer_prefers_bullets` and `test_writer_drops_tabular_phrase`).
- `test_writer_has_few_shot_examples` — **keep** (still has `<example>` / `EXEMPLO`, just with different content).
- `test_writer_has_inviolable_rules` — **update**. v2 asserted presence of *"interpretações pessoais"*. v3 collapses that rule into *"nunca invente"* (item 2 of new REGRAS). Replace the `interpretações pessoais` check with one of: `"nunca invente"`, `"não invente"`, or `"nunca invente"`. Keep the other asserts ("nunca arredonde", "CFR", "FOB", "DATA NÃO ESPECIFICADA") as-is.
- `test_writer_has_no_classification_tags` — **keep**.

### Manual validation

Before merging, the implementer should:
1. Run the pipeline against 5 samples from Redis (the ones surfaced in brainstorming):
   - Long IODEX rationale (4.376 chars)
   - Medium IODEX rationale (1.574 chars)
   - Short Lump Premium rationale (733 chars)
   - Repetitive trade dump (2.620 chars)
   - Hoa Phat news (1.227 chars)
2. Record line count of final Curator output for each.
3. Verify:
   - All 5 outputs ≤ 25 visible lines
   - All numbers present in the output match the source exactly (spot check 3 numbers per sample)
   - No boilerplate strings ("Platts is part of S&P", "applies to market data code") slipped through
   - News samples (1, 2, 5) fall in 18-22 line range; trade dump (4) compresses hardest; short rationale (3) stays short.

Running these 5 samples isn't automated in this cycle — it's a manual checklist the implementer follows before pushing. Results go in the PR description.

---

## Risks and trade-offs

1. **Model may still ignore the budget.** Budgets are notoriously soft for LLMs. The Curator's hard ceiling is the backstop.
2. **Compression may lose a number that mattered.** The new Critique items 6 (inchado) and 7 (invenção) help, but Critique item 1 (essência) is softer than v2's "completeness" check. Manual validation catches regressions.
3. **Trade dump case is fragile.** The repetitive-entry input (sample 4) depends on the model recognizing the repetition pattern. Exemplo 2 in Writer v3 must demonstrate this consolidation clearly.
4. **Two philosophies in one pipeline.** Rationale vs. news vs. trade-dump have different "right answer" shapes. The budget section acknowledges this with per-type hints; we accept some variance.

---

## Rollout

1. Update prompts + tests in one PR (no schema migration, no feature flags).
2. Manual validation against 5 Redis samples before push.
3. After merge, watch the first 3-5 production pipeline runs and look for:
   - Line count in final message
   - Any "Platts is part of" leaking
   - Any invented number (compare to archived `fullText`)

If a regression shows up, revert the prompts file; no DB or state rollback needed.

---

## Out of scope (explicit)

- Adjuster prompt — unchanged.
- Pipeline architecture, retry logic, Claude model selection.
- WhatsApp visual format (header, formatting rules, emoji policy).
- Automating the 5-sample validation (future: add a `tests/integration/` runner that calls Claude with fakes or recorded outputs).
