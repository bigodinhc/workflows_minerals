# Agent Prompts v5 — Design Doc

**Topic:** Evolve Writer (v4 → v5), Curator (v3 → v4), and Adjuster (v2 → v3) to support classified-news pipeline with 5 types, tight formatting contracts, and template-aware adjustment.

**Date:** 2026-04-21

**Status:** Draft — awaiting user review.

---

## Problem

v4 (Writer) / v3 (Curator) draft introduced classification (6 types) and template routing, which is the right direction. Reviewing against production needs surfaced gaps that either under-specify behavior or create drift between the two agents:

1. **EVENTO_CRITICO is a weak type.** Breaking news is an urgency signal, not a structural class. Every real breaking belongs to PRICING_SESSION (price/freight impact), COMPANY_NEWS (entity action), or ANALYTICAL (structural driver). Keeping EVENTO_CRITICO invites misclassification and duplicates rules.
2. **Classification is ambiguous at the margins.** "Choose dominant angle" fails on Vale earnings with IODEX commentary, CISA releases, or Platts rationale with a paragraph of analysis.
3. **Writer has no proibido list.** The Curator filters "significativo / substancial / dinâmica observada" — but only if the Writer produced it. Filtering at the Writer stops the problem at the source.
4. **Size targets measure Writer output in Curator units.** "18-22 linhas no WhatsApp" can't be counted by an agent that does not render WhatsApp. The Curator adds section asterisks, blank lines between heterogeneous bullets, blockquote markers — the Writer cannot predict line count.
5. **`Watch:` is under-specified.** Both agents use it but neither pins format (prose vs bullet vs section, bold/mono, prefix).
6. **DRIVER section is under-specified.** When to extract vs inline is judged by the model without criteria — v4 examples diverge (ANALYTICAL extracts, COMPANY_NEWS inlines the same kind of content).
7. **Curator has no data-sourcing rule for the header date.** Example shows `21/ABR` but nothing says where that comes from when the original has no date or has a date in English.
8. **Curator blank-line-between-bullets is inconsistent across its own examples.** Readers get mixed signal.
9. **COMPANY_NEWS "ativo dominante"** leaves Vale / BHP / mixed releases undefined.
10. **DIGEST has no fallback** when the input has 3-5 headlines instead of 6+.
11. **Curator FUTURES example has wrong numbers.** Input says `Apr v May 0.40, May v Jun 0.60`; output shows `$0,40-0,45` and `$1,50-1,60`. Few-shot rot.
12. **Adjuster is v4-blind.** It receives the formatted message but has no rule to preserve the template-implicit structure introduced in v4.

---

## Goal

Produce Writer v5 / Curator v4 / Adjuster v3 where:

- Classification is deterministic (ordered decision rules, no tie-breakers).
- Writer speaks in units it controls (bullets + sections), never WhatsApp lines.
- Writer and Curator share a single proibido list, filtered at the source.
- `Watch:`, `DRIVER`, data-sourcing, blank-line rule, ativo dominante, DIGEST fallback all have pinned rules.
- Adjuster preserves template-implicit structure unless user requests a structural change.
- Few-shot examples are consistent with rules and free of factual errors.

**Non-goal:** changing the pipeline architecture, adding new agents, or changing message visual identity (header, emoji, dividers).

---

## Architecture

No structural changes. Pipeline stays:

```
raw_text (title + date + source + fullText, English)
  → Writer v5    (classify + synthesize, PT-BR)
  → Critique v3  (unchanged)
  → Curator v4   (template-route + WhatsApp format)
  → final message → Telegram

Post-send feedback:
  final message + editor feedback → Adjuster v3 → adjusted message
```

Files affected:
- `execution/core/prompts/writer.py` — body rewrite (v5)
- `execution/core/prompts/curator.py` — body rewrite (v4)
- `execution/core/prompts/adjuster.py` — additive rules (v3)
- `execution/core/prompts/critique.py` — no change (v3 stays)
- `execution/core/prompts/__init__.py` — docstring fix ("3-agent pipeline" → "Writer → Critique → Curator pipeline + post-send Adjuster")

---

## Writer v5

### Type set: 5 (remove EVENTO_CRITICO)

```
PRICING_SESSION   FUTURES_CURVE   COMPANY_NEWS   ANALYTICAL   DIGEST
```

Breaking news is re-routed by angle, not by urgency flag:
- Impact on price/freight/levels → `PRICING_SESSION`
- Action of a named entity (company, government, association) → `COMPANY_NEWS`
- Structural mechanism/driver analysis → `ANALYTICAL`

Urgency lives inside the **lead** (tension-first phrasing), not in a separate type.

### Classification — ordered decision rules

First rule that matches wins. No tie-breakers, no dominant-angle judgment calls.

```
1. Round-up / newswire wrap com ≥3 headlines independentes
   de entidades ou temas distintos (Metals Monitor, wrap, etc)?
   → DIGEST

2. Sujeito principal é uma única empresa/associação/órgão
   com release sobre si (earnings, guidance, M&A, produção trimestral)?
   → COMPANY_NEWS

3. Texto traz trades físicos do dia, brand adjustments,
   ou níveis absolutos de índice daily (Platts/MB/TSI)?
   → PRICING_SESSION

4. Texto traz curva de swaps/futuros, movimento intraday
   SGX/DCE/SHFE, spreads entre contratos, morning/close wrap?
   → FUTURES_CURVE

5. Texto compara regiões, períodos, ou analisa driver estrutural
   (matriz de insumo, price gap, freight mechanism)?
   → ANALYTICAL
```

If no rule matches (rare — unclassifiable text), default to `ANALYTICAL` and lead with what the text actually says.

Rationale for DIGEST first: a round-up about Vale + Tokyo Steel + Indonesia would incorrectly match rule 2 (COMPANY_NEWS) if evaluated before DIGEST. Horizontal multi-entity scan beats any single-entity rule.

### Proibido list (shared with Curator)

Writer refuses to emit these expressions in its output:

```
"significativo", "substancial", "notável", "robusto",
"dinâmica observada", "dinâmica de", "cenário de",
"registrou alta", "registrou queda", "em meio a"
```

Rule: replace with concrete verb + number (ex: "forte" → actual %, "registrou alta" → "subiu").

### Size targets — in bullets + sections

Writer measures its own output, not Curator-rendered lines:

| Tipo | Bullets | Seções | Notas |
|---|---|---|---|
| PRICING_SESSION | 8-12 | 3-5 | Conta lead como 0 bullets |
| FUTURES_CURVE | 6-10 | 2-4 | Citação `>` não conta como bullet |
| COMPANY_NEWS | 10-14 | 3-4 | `Watch:` não conta |
| ANALYTICAL | 7-10 | 3 | DRIVER sempre uma seção quando aplicável |
| DIGEST | 3-12 headlines | 1-5 grupos | Bullets = headlines. 3-5 headlines → 1-2 grupos. 6+ → 3-5 grupos. |

If original is short (< ~150 words), output is short — do not pad to hit the floor.

### `Watch:` format (pinned)

- Linha única em prosa.
- Prefixo literal `Watch:` (com dois-pontos).
- **Sem** header em CAPS, **sem** bullet `-`, **sem** bold/mono.
- Só aparece se o original aponta catalyst específico (data, evento, release futuro).
- Posição: última linha da síntese.

Example:
```
Watch: feriado May Day (1-5/mai) pode puxar restock.
```

### `DRIVER` heuristic

Extract as `DRIVER` section when **the mechanism IS the central angle of the thesis**.

Inline (dentro de outra seção) when **the mechanism is an accessory note** to a different thesis.

Test: ask "if I remove the mechanism, does the lead still make sense?"
- If no → mechanism é a tese → `DRIVER` section.
- If yes → mechanism é acessório → inline.

Examples:
- ANALYTICAL "Norte +Rs 2k vs Leste +Rs 900" — remove matriz de insumo, lead colapsa → `DRIVER` section.
- COMPANY_NEWS Severstal "margem colapsou" — remove "frete mais caro empurra export" e tese segue → inline.

### DIGEST rules (unchanged from v4, restated)

- Lead destaca 2-3 itens de maior impacto, não resume tudo.
- Agrupa headlines por tema (IRON & STEEL, MACRO/FRETE, CRÍTICOS & BASE METALS, M&A, etc).
- Cada headline: 1-2 linhas, formato "Entidade — fato + número".
- Sem blockquotes.
- Sem `DRIVER` section.
- `Watch:` opcional, só se há temporal claro.

### Other unchanged rules

Tudo o mais de v4 mantido: lead afiado (tensão), descrever-não-interpretar, bullets por default, lista "O QUE SEMPRE CORTAR", regras inegociáveis (números exatos, nunca invente, terminologia técnica, data ausente).

### Output format (unchanged from v4)

```
[TIPO: PRICING_SESSION | FUTURES_CURVE | COMPANY_NEWS | ANALYTICAL | DIGEST]
[TÍTULO: título de 5-8 palavras, específico, com movimento/ação]

<lead em prosa, 1-2 frases curtas com tensão>

SEÇÃO EM CAPS (sem asterisco — formatação é do Curator)
- bullet
- bullet
> citação se houver info nova

SEÇÃO EM CAPS
- bullet

Watch: próximo catalyst, se aplicável.
```

### Examples

Mantém os 5 exemplos atuais de v4: PRICING_SESSION, FUTURES_CURVE, COMPANY_NEWS, ANALYTICAL, DIGEST. Remove qualquer referência a EVENTO_CRITICO.

---

## Curator v4

### Template set: 5 (remove EVENTO_CRITICO)

Templates idem Writer: `PRICING_SESSION`, `FUTURES_CURVE`, `COMPANY_NEWS`, `ANALYTICAL`, `DIGEST`.

### Header — data rule

```
📊 *MINERALS TRADING*
*<título do Writer>*
`<ATIVO> · <DD/MMM em PT-BR>`
─────────────────
```

Data resolution:
1. Use data do input original (assinatura no texto, data do rationale, data de closing).
2. Se Writer marcou `[DATA NÃO ESPECIFICADA]`, usar data de execução do pipeline (hoje).
3. Formato **DD/MMM em PT-BR**: `21/ABR`, `14/JUN`, `03/SET` — nunca `21/APR`, `14/JUN` em inglês.

Month abbreviations (PT-BR caps, 3 letras):
```
JAN FEV MAR ABR MAI JUN JUL AGO SET OUT NOV DEZ
```

### Header — ativo da pílula (linha 3)

| Tipo | Ativo |
|---|---|
| PRICING_SESSION | `IRON ORE` (ou commodity específica: `COKING COAL`, `REBAR` etc) |
| FUTURES_CURVE | `IRON ORE FUTURES` (ou `<COMMODITY> FUTURES`) |
| COMPANY_NEWS | ver regra de ativo dominante abaixo |
| ANALYTICAL | produto em foco (`REBAR`, `HRC`, `IRON ORE`, etc) |
| DIGEST | `DIGEST` |

### COMPANY_NEWS — ativo dominante

Regra em ordem:
1. Empresa siderúrgica pura (Severstal, POSCO, Tata Steel) → `STEEL`.
2. Mineradora diversificada (Vale, BHP, Rio Tinto, Anglo American): usar ativo que domina o release. Default: `IRON ORE`. Se release foca copper/nickel/coal, use esse.
3. Empresa de um commodity específico (Paladin/uranium, Freeport/copper) → esse commodity.
4. Release consolidado sem foco claro → categoria ampla: `MINING` ou `STEEL`.

### `Watch:` — render

- Preservar prefixo literal `Watch:`.
- Render como prosa normal.
- **Sem** bold, **sem** mono inline, **sem** bullet `-`.
- Posição: última linha útil da mensagem.
- Se Writer não entregou `Watch:`, não invente.

### Blank line between bullets

Regra determinística:

- **Heterogêneo** (cada bullet representa entidade/evento distinto) → linha em branco entre cada bullet.
  - Exemplo: trades de produtores diferentes (BHP, Rio Tinto), blocos macro não relacionados.
- **Homogêneo** (lista da mesma natureza — ex: todos brand adjustments de um mesmo dia, todos valores de port-stock) → compacto, sem linha em branco.
  - Exemplo: `*BRAND ADJUSTMENT*` com NHGF, JMBF, PBF.

Heurística: se cada bullet começa com uma entidade/rótulo diferente que o leitor escaneia, heterogêneo. Se cada bullet é um item da mesma lista, compacto.

### DIGEST — bloco count depends on headline count

Reads Writer output and sizes the template accordingly:

- **6+ headlines** → 3-5 blocos temáticos (full DIGEST).
- **3-5 headlines** → 1-2 blocos (reduced DIGEST). Same template, fewer blocks.
- **<3 headlines** → Writer should not have classified as DIGEST per rule 1. If it did (out-of-spec output), Curator falls back: reclassify to ANALYTICAL (drop `*DIGEST*` ativo, replace pill with first headline's commodity; keep content as bullets under one `*MERCADO*` block).

### Proibido list (mesma do Writer)

```
"significativo", "substancial", "notável", "robusto",
"dinâmica observada", "dinâmica de", "cenário de",
"registrou alta", "registrou queda", "em meio a"
```

Regra: se Writer entregou alguma dessas, Curator reescreve no momento da formatação.

### Hard ceiling by type

| Tipo | Máx linhas WhatsApp (header + corpo) |
|---|---|
| PRICING_SESSION | 30 |
| FUTURES_CURVE | 25 |
| COMPANY_NEWS | 30 |
| ANALYTICAL | 25 |
| DIGEST | 25 |

Ordem de corte se estourar:
1. Seção que menos move decisão.
2. Blockquote (se ainda estourar).
3. **Nunca cortar**: header, lead, dados numéricos principais, `Watch:`.

### Other rules unchanged from v3

Mono inline (`` `valor` ``) para todo número relevante, nunca envolver mensagem em triple-backticks, nunca `###`, emoji só 📊 no header, headers de seção em `*CAPS*` precedidos de linha em branco.

### Examples — 5 (remove EVENTO_CRITICO)

Mantém: PRICING_SESSION, FUTURES_CURVE, COMPANY_NEWS, ANALYTICAL, DIGEST.

**FUTURES_CURVE fix** — corrigir o Exemplo 2. Input original diz `Apr v May 0.40, May v Jun 0.60`. Output da seção `*SPREADS*` passa a ser:

```
*SPREADS*

- Abr/Mai — `$0,40` · Mai/Jun — `$0,60` backwardation
- Q2'26/Q3'26 — `$1,55`
- 65/62% Abr — `$17,75` · Mai — `$16,50`
```

(v3 mostrava `$0,40-0,45` e `$1,50-1,60` — não batia com input. v4 refaz com os valores exatos.)

---

## Adjuster v3

Adjuster recebe a mensagem final formatada + feedback do editor. Ele **não** vê os metadados `[TIPO: ...]` do Writer — eles foram consumidos pelo Curator.

### New rules (additive to v2)

1. **Preserve template-implicit structure.** A mensagem tem um template atrás dela (5 possíveis, lidos pelo Curator a partir do tipo). Não mova seções, não inverta ordem, não funda headers distintos em um só. Aplique apenas o ajuste solicitado.

2. **Preserve `Watch:` line.** Se existir, mantenha na última posição, prefixo literal `Watch:`, sem formatação especial. Não adicione `Watch:` novo salvo pedido explícito. Não reescreva `Watch:` salvo pedido explícito.

3. **Preserve header date.** Não mude `DD/MMM` do header salvo pedido explícito.

4. **Preserve ativo da pílula** (linha 3 do header) salvo pedido explícito.

5. **Preserve blank-line pattern.** Onde a mensagem tem linha em branco entre bullets (heterogêneo), mantenha. Onde está compacto (homogêneo), mantenha.

### Unchanged from v2

Regras 1-8 originais ficam: aplicar apenas ajustes solicitados, formatação WhatsApp nativa, preservar header, manter estilo, preservar números não questionados, não converter tabelas em prosa, output sem comentários, escrita humanizada.

---

## Critique v3 — no change

Critique stays as v3. The type-awareness lives in Writer (produces tipo) and Curator (routes by tipo). Critique operates on the Writer output before type-based formatting, so its essence-preservation checklist is agnostic to template.

Future consideration (out of scope for this spec): whether Critique should also be type-aware, so e.g. a `DIGEST` critique checks for thematic grouping instead of lead afiado. Deferring until we have production signal.

---

## Testing

### Unit — `tests/test_prompts.py`

Update assertions:
- `WRITER_SYSTEM` contains `[TIPO: PRICING_SESSION`, `[TIPO: FUTURES_CURVE`, `[TIPO: COMPANY_NEWS`, `[TIPO: ANALYTICAL`, `[TIPO: DIGEST`.
- `WRITER_SYSTEM` does **not** contain `EVENTO_CRITICO`.
- `WRITER_SYSTEM` contains the proibido list (at least one sentinel: `"significativo"` and `"dinâmica observada"`).
- `CURATOR_SYSTEM` contains exactly the 5 types above.
- `CURATOR_SYSTEM` does **not** contain `EVENTO_CRITICO`.
- `CURATOR_SYSTEM` contains data rule sentinel (`"DD/MMM"`, `"ABR"`, or similar).
- `ADJUSTER_SYSTEM` contains `"Preserve"` or `"Watch:"` sentinel.
- FUTURES example in `CURATOR_SYSTEM` contains `$0,60` and does **not** contain `$0,40-0,45`.

### Integration — fixture regression

Run the pipeline end-to-end on 3-5 archived Redis fixtures spanning types:
- Platts iron ore rationale (PRICING_SESSION)
- SGX wrap (FUTURES_CURVE)
- Severstal earnings (COMPANY_NEWS)
- Metals Monitor (DIGEST)
- Rebar India comparative (ANALYTICAL)

For each: check Writer emits `[TIPO: ...]` line 1, Curator produces header with correct ativo and date, message respects hard ceiling.

### Manual smoke

Generate one of each type on current archived inputs and visually inspect on WhatsApp (or Telegram) for: no triple-backtick wrap, correct mono inline, blank-line pattern, `Watch:` position.

---

## Migration

Single PR. All four prompt files (writer, curator, adjuster, `__init__.py`) bumped together to preserve pipeline consistency. No runtime flag — v5/v4/v3 replaces v4/v3/v2 atomically.

No data migration. No schema change. No breaking change to downstream consumers (final message shape identical; only internal quality improves).

---

## Risks

1. **Classification drift in production.** Model may misclassify ambiguous inputs. Mitigation: ordered decision rules reduce ambiguity; fallback to `ANALYTICAL` for unclassified.
2. **Few-shot rot.** v3 had wrong numbers in FUTURES example and nobody noticed until this review. Mitigation: unit test pins corrected values; add manual smoke step to release checklist.
3. **Adjuster over-preserving.** "Preserve template" may block legitimate editor requests. Mitigation: "salvo pedido explícito" escape clause in every preservation rule.
4. **Proibido list too aggressive.** "em meio a" can be legitimate in some contexts. Mitigation: list is intentionally tight (10 items), targets trader-voice failures, not general PT-BR.
5. **Size targets in bullets+sections drift from user-visible "too long".** Writer hits target, Curator still renders 30-line message. Mitigation: Curator hard ceiling + ordem de corte.

---

## Open questions (resolved during brainstorm)

- ~~Should breaking news have a dedicated type?~~ No — angle-based reclassification. (Resolved.)
- ~~Keep v4/v3 numbering or bump?~~ Bump: Writer v5, Curator v4, Adjuster v3. (Resolved.)
- ~~Size targets in WhatsApp lines or bullets+sections?~~ Bullets + sections. (Resolved.)
- ~~Palavras proibidas list extension?~~ Stay with proposed 10. (Resolved.)

---

## Out of scope (future iterations)

- Critique type-awareness.
- Per-type tone variants (e.g., COMPANY_NEWS more formal than FUTURES_CURVE).
- Automated type-correctness eval (LLM-as-judge on classification).
- Adjuster awareness of original `[TIPO: ...]` (would require Curator to pass metadata through).
