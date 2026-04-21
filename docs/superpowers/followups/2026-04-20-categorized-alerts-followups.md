# Categorized Alerts — Follow-ups

**Plan:** `docs/superpowers/plans/2026-04-20-categorized-alerts-plan.md`
**Shipped:** 2026-04-20 on `main` (commits `eb02f2b..c458ddae`)
**Final review verdict:** Ship with followup list

Source: final `superpowers:code-reviewer` audit after all 7 tasks completed.

## Priority 1 — Circuit-breaker skipped-bucket outranks the real cause — **✅ SHIPPED 2026-04-20 (commit `75cc710`)**

Resolved via Option A: new `SendErrorCategory.SKIPPED_CIRCUIT_BREAK` enum value, filtered from main grouping, rendered as trailing footnote `"ℹ️ N contatos pulados pelo circuit breaker"`. Real cause + action hint now always appears at the top. 3 new tests + 1 updated test.

---

**Original scenario (for history):** 5 real `WHATSAPP_DISCONNECTED` + 15 skipped contacts (UNKNOWN, `error="skipped_due_to_circuit_break"`). Current sort-desc-by-count renders:

```
• 15× Erro não categorizado → AÇÃO: Veja logs do GitHub Actions
• 5× WhatsApp desconectado → AÇÃO: Reconecte QR em mineralstrading.uazapi.com
```

Operator sees the wrong action hint first, and "Erro não categorizado" is misleading (these rows are skipped, not uncategorized). This directly undermines the plan's motivation.

**Fix options:**
- **A (preferred):** introduce `SendErrorCategory.SKIPPED_CIRCUIT_BREAK` with label `"Ignorados (circuit breaker)"`, no action hint. Render as a **trailing clarification line** below the main category groups, NOT as a sortable bucket.
- **B:** sort key becomes `(is_skip_bucket, -count)` so skip entries always sort after real failures.

**Files:** `execution/core/delivery_reporter.py` (enum, dispatch skip branch, formatter grouping), `tests/test_delivery_reporter.py` (circuit breaker + formatter tests).

**Estimate:** 30-45 min TDD.

---

## Priority 2 — Collapse `_categorize_error` + `classify_error` duplicated JSON parsing

`DeliveryReporter.dispatch` currently runs both on every exception:

```python
category, _reason = classify_error(exc)        # parses JSON body, extracts reason
error = _categorize_error(exc)                  # parses JSON body AGAIN, returns "HTTP N: reason" string
```

Drift risk: a future tweak to one's JSON extraction logic that forgets the other silently desyncs dashboard JSON from Telegram category.

**Fix:** either have `classify_error` return `(category, reason, legacy_error_string)` in one pass, or introduce `_legacy_error_string(exc, category, reason)` that composes from already-extracted reason. Remove the duplicated JSON-parse block. Tests at `test_delivery_reporter.py:524-542` rewrite to target the new path.

**Estimate:** 45 min.

---

## Priority 3 — Module structure: imports scattered, "7 tasks bolted together" seam

`execution/core/delivery_reporter.py` has imports at lines 8-10, then dataclasses, then another import block at line 59-61 (enum + typing). The second import block is a visible seam from the task-by-task construction.

**Fix:** reorder to: imports (top), enum + constants (labels/hints/fatal), classifiers, helpers, dataclasses, class. Keep deferred `requests/json` imports inside functions (justified — startup time).

Unlocks removing the `"SendErrorCategory"` string forward-reference in `DeliveryResult.category` (with its `# type: ignore[assignment]` + `__post_init__` coercion) and in `fatal_categories: "frozenset[SendErrorCategory]"` — both become plain annotations after the reorder.

**Estimate:** 20 min, no behavior change, pure readability.

---

## Priority 4 — Sentry tag namespace asymmetry

Currently: `send.error_category` (prefixed) + `workflow` (unprefixed). Sentry treats both as tags but consistency helps discoverability.

**Suggestion:** either both prefixed (`send.category`, `send.workflow`) or keep current split (`send.category` + unprefixed `workflow`) since "workflow" may later become a first-class Sentry dimension used across other products.

**Fix:** rename at `execution/core/delivery_reporter.py:476-477`, update 3 tests that reference the tag key. Do before third-party tooling / alert rules start depending on the current names.

**Estimate:** 10 min.

---

## Priority 5 — Split `tests/test_delivery_reporter.py` (currently 811 lines)

File is still navigable but imports are scattered throughout (lines 2, 50-52, 142-143, 202, 453). `_mock_http_error` helper defined at line 515 after many tests that would have benefited from it.

**Suggestion:** when the next round of tests lands, split into:
- `tests/test_classifier.py` — `SendErrorCategory` + `classify_error` + `_categorize_error`
- `tests/test_dispatch.py` — `DeliveryReporter.dispatch` behavior
- `tests/test_telegram_message.py` — `_format_telegram_message` + grouping
- `tests/test_circuit_breaker.py` — circuit breaker scenarios
- `tests/test_sentry_tagging.py` — `_capture_sentry` behavior

Each stays under ~200 lines and test intent is obvious from filename.

**Estimate:** 30 min, mechanical split.

---

## Priority 6 — Telegram Markdown (legacy) special-char escaping in contact names

`_send_telegram_summary` uses `parse_mode="Markdown"` (legacy). Contact names with `_`, `*`, `[`, `` ` `` in the sample-names inline render (`"({names})"` when group ≤3) will break rendering.

**Pre-existing**, not a regression from this plan. Flag for the next Telegram formatting touch-up; consider migrating to `MarkdownV2` with proper escaping, or to HTML `parse_mode`.

**Estimate:** 20 min.
