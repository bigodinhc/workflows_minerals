# Bot `/queue` Bulk Actions — Design

**Status:** Draft · 2026-04-22

## Goal

Allow the admin to select multiple items from `/queue` staging and archive-or-discard them in one action, instead of opening each item's card and clicking archive one by one.

## Background

Today, `/queue` renders each staging item as a single inline button. Clicking it opens the curation card (`queue_open:<id>`) — the only way to archive is card by card, which is slow when staging has 10+ items.

The contact admin already has a bulk activate/deactivate pattern (`ContactBulk` → `ContactBulkConfirm`/`ContactBulkCancel`) that this feature reuses.

## User-Facing Behavior

### Normal mode (current)

Unchanged. Each item is a single button. Clicking opens the curation card.

### Entering select mode

A new button `☑️ Modo seleção` appears at the top of `/queue` (above the item list, above pagination). Clicking enters select mode.

### Select mode layout

```
*🗂️ STAGING · 2 selecionados de 7 · coletados 05:00–07:00 BRT*

[ ☑️ 🗞️ Title 0 🕐07:00 ]   (toggle button — callback: queue_sel_toggle:<id>)
[ ☐ 🗞️ Title 1 🕐06:00 ]
[ ☑️ 🗞️ Title 2 🕐05:00 ]
...

[ ✅ Todos ] [ ❌ Nenhum ]
[ 📦 Arquivar 2 ] [ 🗑️ Descartar 2 ]
[ 🔙 Sair ]

[ ⬅ anterior ] [ 1/2 ] [ próximo ➡ ]   (pagination — only if total_pages > 1)
```

- Item buttons toggle: `☐` → `☑️` and back. Clicking re-renders the message in place (edit_message_text).
- `✅ Todos` marks every item across **all pages** (reads all staging ids). `❌ Nenhum` clears the selection.
- `📦 Arquivar N` and `🗑️ Descartar N` show N = total selected across all pages. Disabled visually when N=0 (button text shows `📦 Arquivar (0)` and a no-op callback — Telegram doesn't support disabled inline buttons, so we keep them visible but they answer with a toast "Nada selecionado" if tapped).
- `🔙 Sair` returns to normal mode AND clears the selection.

### Pagination in select mode

Page navigation preserves the current selection (stored server-side). Item rows on page 2 show their own checkbox states based on the persisted set.

### Confirmation

Clicking `📦 Arquivar 5`:

```
Arquivar 5 items?

[ ✅ Sim ] [ ❌ Cancelar ]
```

- `✅ Sim` → executes, replies with a result toast (`✅ 5 arquivados` or `✅ 4 arquivados, 1 falhou` for partial), auto-exits select mode, re-renders `/queue` at page 1.
- `❌ Cancelar` → goes back to the select-mode view (selection preserved).

Discard follows the same flow with `🗑️ Descartar N items?`.

### Auto-exit

After a successful bulk archive or discard (even partial), the mode is cleared and `/queue` re-renders in normal mode.

## State Model

Selection state is **volatile and per-chat** — it does not need to survive bot restarts.

### Redis keys

| Key | Type | TTL | Purpose |
|-----|------|-----|---------|
| `bot:queue_mode:{chat_id}` | string `"select"` | 10 min | Presence = select mode active. Absence = normal mode. |
| `bot:queue_selected:{chat_id}` | Set of item_ids | 10 min | Items currently selected. |

Both keys are refreshed to full TTL on every state mutation (toggle, select-all, page navigation in select mode). The mode key exists to make "am I in select mode?" queryable without reading the set.

### Selection lifecycle

- **Enter mode:** create both keys with empty set.
- **Toggle item:** add/remove from set, refresh TTL.
- **Select all:** overwrite set with all current staging ids, refresh TTL.
- **Clear all:** delete selected key, keep mode key.
- **Exit mode (user or auto):** delete both keys.
- **TTL expired:** next callback sees no mode key → falls back to normal rendering.

### Cross-page safety

The set holds every selected id regardless of page. If an item was selected but archived/discarded by another process (unlikely but possible — admin-only), it's silently pruned from the set on next render because `list_staging()` won't include it.

## New CallbackData classes

Following the existing pattern in `webhook/bot/callback_data.py`:

```python
class QueueModeToggle(CallbackData, prefix="q_mode"):
    """Enter or exit select mode from normal /queue header."""
    action: str  # 'enter' | 'exit'


class QueueSelToggle(CallbackData, prefix="q_sel"):
    """Toggle selection of a single item (only valid in select mode)."""
    item_id: str


class QueueSelAll(CallbackData, prefix="q_all"):
    """Select every staging item (all pages)."""
    pass


class QueueSelNone(CallbackData, prefix="q_none"):
    """Clear selection (keeps select mode active)."""
    pass


class QueueBulkPrompt(CallbackData, prefix="q_bulk"):
    """First tap on archive/discard — shows confirmation."""
    action: str  # 'archive' | 'discard'


class QueueBulkConfirm(CallbackData, prefix="q_bulkok"):
    """User confirmed — execute the action on current selection."""
    action: str  # 'archive' | 'discard'


class QueueBulkCancel(CallbackData, prefix="q_bulkno"):
    """Cancel confirmation, return to select mode."""
    pass
```

(Callback data prefixes are short — Telegram limits total callback payload to 64 bytes.)

## New module: `webhook/queue_selection.py`

Thin wrappers around Redis for the selection state. Pure I/O, no formatting.

```python
def enter_mode(chat_id: int) -> None: ...
def exit_mode(chat_id: int) -> None: ...
def is_select_mode(chat_id: int) -> bool: ...
def get_selection(chat_id: int) -> set[str]: ...
def toggle(chat_id: int, item_id: str) -> bool: ...  # returns new selected-state
def select_all(chat_id: int, item_ids: list[str]) -> None: ...
def clear(chat_id: int) -> None: ...
```

Both keys share the module-level TTL constant `_TTL_SECONDS = 600`. Every mutating call refreshes TTL (pipeline: set/sadd/expire).

## Changes to `execution/curation/redis_client.py`

Add two bulk helpers. Each reuses a single pipeline for atomicity inside the batch, but items are independent — one bad item does not abort the others.

```python
def bulk_archive(item_ids: list[str], date: str, chat_id: int) -> dict:
    """Archive each item. Returns {'archived': [...ids], 'failed': [...ids]}.

    Missing/expired items go into 'failed' (None from get_staging).
    Exceptions during one item's pipeline go into 'failed' too.
    """

def bulk_discard(item_ids: list[str]) -> int:
    """Delete each staging key. Returns count of keys actually deleted."""
```

Implementation reuses the existing single-item `archive()` and `discard()` under the hood — no new atomicity semantics per item.

## Changes to `webhook/query_handlers.py`

`format_queue_page` grows a new signature:

```python
def format_queue_page(
    page: int = 1,
    mode: str = "normal",            # 'normal' | 'select'
    selected: set[str] | None = None,
) -> tuple[str, Optional[dict]]: ...
```

Old callers pass `mode="normal"` implicitly (default). The function branches:

- `normal`: current behavior (unchanged).
- `select`: header shows `N selecionados de M`, item rows show checkbox prefix, action rows (`Todos/Nenhum`, `Arquivar/Descartar`, `Sair`) appended before pagination.

The `☑️ Modo seleção` entry button is added in normal mode only, in a new row above item buttons.

## Changes to `webhook/bot/routers/callbacks_queue.py`

Add handlers for the 7 new callbacks. `QueuePage` (existing) reads the current mode and passes it to `format_queue_page`.

All handlers:
1. Read mode + selection from `queue_selection`.
2. Mutate state as needed.
3. Re-render via `edit_message_text` with the new markup.

Handlers run `redis_client.bulk_archive`/`bulk_discard` via `asyncio.to_thread` to stay non-blocking (same pattern as the single-item handler).

Result toast after bulk action:
- All succeeded: `✅ 5 arquivados` / `✅ 5 descartados`
- Partial: `✅ 4 arquivados, 1 falhou (expirado ou já removido)`
- All failed: `⚠️ Nenhum item arquivado (todos expiraram ou foram removidos)`

## Error Handling

- **Redis unavailable during toggle:** log error, answer callback with `⚠️ Redis indisponível`, leave state unchanged.
- **Empty selection when clicking `Arquivar N`:** prevented by the button text — but if the user somehow hits it with N=0, answer with toast `"Nada selecionado"` without editing the message.
- **Selection TTL expired mid-flow:** next callback sees empty selection → answers toast `"Seleção expirou, entre no modo novamente"` and exits mode.
- **Bulk archive partial failure:** reported in the result toast; the re-rendered `/queue` naturally shows only items still in staging.

## Testing

New test files:
- `tests/test_queue_selection.py` — unit tests for the selection state module (toggle, TTL, select-all, clear, mode lifecycle).

Existing test files extended:
- `tests/test_query_handlers.py` — `format_queue_page(mode="select", selected={...})` renders checkboxes, header shows selected count, action buttons present.
- `tests/test_callbacks_queue.py` (or new if missing) — the 7 new callback handlers.

Bulk helpers in redis_client covered by existing redis_client tests (pattern: set up 3 staging items, call `bulk_archive(["a","b","missing"])`, assert `{archived: [a,b], failed: [missing]}`).

## What This Is Not

- **No "reject with reason" in bulk.** That would require a text-collection step per item.
- **No selection persistence across bot restarts.** Acceptable — state is short-lived.
- **No keyboard shortcuts or rich mini-app UI.** This is a pure Telegram inline-keyboard feature.
- **No change to the existing single-item flow** (`queue_open:<id>` stays). Users who want to read a card before archiving still open it one by one in normal mode.
