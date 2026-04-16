# Agent Prompts v2 — Design Spec

## Problem

The 3-agent pipeline (Writer → Critique → Curator) produces output that is:
- Too long (40+ lines for simple news)
- AI-sounding ("registrou alta subsequente", "dinâmica observada", "liquidez adequada")
- Converts tabular data into prose (trades listed as paragraphs instead of aligned tables)
- Leaks internal metadata ([CLASSIFICAÇÃO], [ELEMENTOS PRESENTES]) into final output
- Verbose prompts (~370 lines inline in app.py) with zero few-shot examples

## Goal

Rewrite all 3+1 prompts to produce shorter, trader-voiced output with proper table formatting, guided by concrete few-shot examples rather than verbose methodology descriptions. Extract prompts from app.py into dedicated files.

## Approach

Keep the 3-agent architecture (Writer → Critique → Curator + Adjuster). Rewrite prompt content only — no changes to `run_3_agents`, `call_claude`, or any pipeline logic.

Research-backed: Anthropic's prompt engineering docs confirm generate→review→refine as the most effective chaining pattern. The primary improvement is replacing verbose instructions with 2-3 few-shot examples per prompt, which the docs identify as the most reliable way to steer output format, tone, and structure.

## Architecture

### Prompt file structure (new)

```
execution/core/prompts/
  __init__.py
  writer.py      → WRITER_SYSTEM
  critique.py    → CRITIQUE_SYSTEM
  curator.py     → CURATOR_SYSTEM
  adjuster.py    → ADJUSTER_SYSTEM
```

Each file exports a single string constant. `app.py` imports from these modules instead of defining inline.

### Writer prompt (90→~40 lines)

**Removed:**
- 4-phase methodology (Identificação Rápida, Classificação Inteligente, Extração Estruturada, Síntese Inteligente)
- Output metadata tags: [CLASSIFICAÇÃO], [ELEMENTOS PRESENTES], [IMPACTO PRINCIPAL]
- Inline "diretrizes para criação de título" section
- Inline "exemplo de processamento"

**Kept (inviolable rules):**
- Precisão absoluta: never round or approximate numbers
- Fidelidade total: no personal interpretations or predictions
- Clareza técnica: preserve market terminology (CFR, FOB, DCE, SGX)
- Honestidade temporal: flag [DATA NÃO ESPECIFICADA] when absent
- Distinção fatos vs especulações

**Added:**
- Direct task instruction (5 lines, no methodology)
- Tabular data rule: if source has trades/prices/volumes → output them as aligned columns, not prose
- Title: 5-8 words, capture tension/action, specific not generic
- 2 few-shot examples:
  - Example 1 (news): "China steel output rises" input → concise analytical output with insight lead, tables for prices/stocks
  - Example 2 (trade summary): IODEX BOTs input → structured tables by category (Fines FOT, Lump FOT, CFR heards, spreads, MOC)

**Output format:**
```
[TÍTULO: 5-8 word title]

[Analytical text in Portuguese, organized in logical sections with *SECTION TITLE* headers. Numerical data in aligned mono blocks. Insight lead. Trader voice.]
```

### Critique prompt (105→~30 lines)

**Removed:**
- 4-dimension framework with percentage weights (40/30/20/10%)
- PONTOS DE EXCELÊNCIA section
- OTIMIZAÇÕES OPCIONAIS section
- RECOMENDAÇÃO DE FORMATO (template, sections, length) — Curator's job
- VERIFICAÇÃO FINAL checklist
- Inline feedback example

**Kept:**
- Data completeness verification (nothing lost from original)
- Data accuracy verification (no numbers altered)
- Title quality check

**Added:**
- Concise checklist (6 items):
  1. Dados completos? Anything from original missing?
  2. Dados corretos? Any number altered/rounded?
  3. Título específico e com tensão? Suggest alternative if generic
  4. Informação mais importante no início?
  5. Dados tabulares em tabela (não prosa)?
  6. Linguagem natural de trader? Flag robotic phrases
- Output format: bullets only, max 15 lines
  - CORREÇÕES: what's wrong
  - FALTANDO: what original has that Writer lost
  - TÍTULO: ok or alternative
  - If all good: "Sem correções."
- Rules: no praise, no format suggestions, be brief

### Curator prompt (155→~60 lines)

**Removed:**
- REGRA DE SILÊNCIO PROFISSIONAL (redundant with output rules)
- Long banned-words list (10+ AI words) — replaced by 1 good/bad pair + few-shot
- Comprimento-alvo section (few-shot examples calibrate naturally)
- Detailed blockquote/bullets rules (examples demonstrate)
- Redundant output instructions

**Kept:**
- Header fixo 4 lines: `📊 *MINERALS TRADING*` / title / `\`ATIVO · DD/MMM\`` / `─────────────────`
- WhatsApp markup rules (bold, mono, blockquote, bullets)
- PROIBIDO list (###, emojis in body, mono wrapper, extra dividers)
- Title rules (5-8 words, specific)

**Added:**
- Tabular data rule (prominent): if Writer delivered numbers → aligned mono table. NEVER convert table to prose. Group duplicate trades into ranges (e.g., ¥766-768).
- Tone: 1 good/bad pair instead of long lists
  - Bad: "as Jimblebar Fines foram negociadas a ¥690/wmt, representando..."
  - Good: "Jimblebar 60,3% bateu ¥690 Tianjin, ¥680 Shandong"
- 2 few-shot examples:
  - Example 1 (news): "China steel output" → final WhatsApp message (~28 lines, insight lead, price table, risk callout)
  - Example 2 (trade summary): IODEX → final WhatsApp message (~60 lines, 8 tables organized by category, consolidation line)

### Adjuster prompt (12→~15 lines)

**Kept:** all current rules (apply only requested adjustments, preserve header, preserve style).

**Added:** "Se a mensagem atual tem tabelas de dados, não converta em prosa ao ajustar."

## Few-shot examples content

The few-shot examples are derived from the mockups validated during brainstorming:

### News example (Writer + Curator)

**Input:** "China steel output rises in April" article (2770 chars) — production data, prices, stocks, export orders.

**Writer output characteristics:**
- Insight lead: "Produção subiu vs março, mas segue abaixo do ano passado"
- Price table in mono block (HRC ¥3.310→¥3.230→¥3.300, Rebar ¥3.150→¥3.070→¥3.100)
- Stock data inline with mono highlights
- Export outlook with risk callout
- ~20 lines

**Curator output characteristics:**
- Title: "China Produz Mais Aço, Mas Demanda Trava"
- Sections: PRODUÇÃO, PREÇOS (table), ESTOQUES, EXPORTAÇÃO
- Trader quote in blockquote
- Risk callout at end
- ~28 lines total

### Trade summary example (Writer + Curator)

**Input:** "Platts Asia Iron Ore Daily Trade Summary" (28647 chars) — 292 lines of trades, heards, offers, bids, assessments, spreads.

**Writer output characteristics:**
- All data preserved in tabular format by category
- Duplicate entries from multiple sources consolidated into price ranges
- Categories: Trades FOT Fines, Trades FOT Lump, Heards CFR, Heards FOT Additional, Lump Heards, Indian Pellet & DRI, Spreads, MOC Assessments
- 1-line insight per category
- ~50 lines

**Curator output characteristics:**
- Title: "IODEX Físico — Sessão DD/MMM"
- Lead with highlight trade + structure
- 8 mono tables by category
- Consolidation insight at end
- ~60 lines total

## Files changed

| File | Change |
|------|--------|
| `execution/core/prompts/__init__.py` | New — empty |
| `execution/core/prompts/writer.py` | New — WRITER_SYSTEM constant |
| `execution/core/prompts/critique.py` | New — CRITIQUE_SYSTEM constant |
| `execution/core/prompts/curator.py` | New — CURATOR_SYSTEM constant |
| `execution/core/prompts/adjuster.py` | New — ADJUSTER_SYSTEM constant |
| `webhook/app.py` | Remove inline prompts (lines 232-598), import from `execution.core.prompts.*` |

## What does NOT change

- `run_3_agents` function signature and flow
- `call_claude` function
- `run_adjuster` function
- `process_news_async` function
- `on_phase_start` callback mechanism
- Model selection (claude-sonnet-4-6)
- Max tokens (4096)
- User prompts passed to each agent (the "Processe...", "Revise...", "Crie..." strings)
- Any Redis, Telegram, or pipeline logic

## Inviolable prompt rules (carried from v1)

These rules MUST appear in the new prompts, verbatim or equivalent:
1. Precisão absoluta: jamais arredonde ou aproxime números
2. Fidelidade total: não adicione interpretações pessoais ou previsões
3. Clareza técnica: mantenha terminologia do mercado (CFR, FOB, DCE, SGX)
4. Honestidade temporal: se não há data, sinalize [DATA NÃO ESPECIFICADA]
5. Distinção clara: separe fatos de especulações/previsões

## Testing

### Manual validation (primary)

1. Process the 3 sample articles through the new pipeline:
   - "China steel output rises" (news, 2770 chars)
   - "Platts Asia Iron Ore Daily Trade Summary" (trade summary, 28647 chars)
   - "China's steel exports fall sharply" (export analysis, 4525 chars)
2. Compare output against mockups approved during brainstorming
3. Verify: no data lost, tables not converted to prose, trader voice, no AI metadata leaked

### Automated tests

- Import tests: verify all 4 prompts importable from `execution.core.prompts.*`
- Content tests: verify inviolable rules appear in each prompt string
- app.py integration: verify `run_3_agents` still works with imported prompts (mock `call_claude`)

## Deploy

Standard push-to-main → Railway auto-deploy. No migration needed — prompt changes are code-only with no state dependencies.

## Out of scope

- Changes to pipeline flow (3-agent count, order, or logic)
- Changes to model selection or max_tokens
- Changes to Telegram posting, Redis, or curation logic
- User prompt strings ("Processe...", "Revise...", "Crie...")
- Adjuster major rewrite (minor addition only)
