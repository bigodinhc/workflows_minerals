# Tests

Python tests for `webhook/` and `execution/` modules.

## Running

```bash
pytest                    # all tests
pytest tests/test_callbacks_curation.py -v
pytest -k "draft_adjust"  # match by test name
```

`tests/conftest.py` puts the repo root and `webhook/` on `sys.path`, so bare imports (`from bot.routers.callbacks import on_draft_adjust`) work. The same imports work at runtime inside the Docker container.

## Mock Pattern for Callback Handlers

Aiogram handlers take a `CallbackQuery` (or `Message`) plus an `FSMContext`. Tests mock all three via fixtures in `conftest.py`.

### Example (characterization test)

```python
import pytest
from bot.callback_data import DraftAction
from bot.routers.callbacks import on_draft_adjust
from bot.states import AdjustDraft

@pytest.mark.asyncio
async def test_draft_adjust_happy_path(mock_callback_query, fsm_context_in_state, mocker):
    # Arrange
    query = mock_callback_query(data="draft:adjust:abc123")
    state = fsm_context_in_state()
    mocker.patch("bot.routers.callbacks.drafts_get",
                 return_value={"message": "hi", "status": "pending"})
    mocker.patch("bot.routers.callbacks.get_bot", return_value=mocker.AsyncMock())

    # Act
    await on_draft_adjust(query, DraftAction(action="adjust", draft_id="abc123"), state)

    # Assert — characterize CURRENT behavior; do not reverse-engineer "should"
    state.set_state.assert_awaited_once_with(AdjustDraft.waiting_feedback)
    state.update_data.assert_awaited_once_with(draft_id="abc123")
    query.answer.assert_awaited_with("✏️ Modo ajuste")
```

### Guidelines

1. **Characterize, don't prescribe.** Match exactly what the handler does today. If a fix is needed, record it as a follow-up — do not alter the handler in the test-writing commit.
2. **Patch at the import site**, not at the definition site. `mocker.patch("bot.routers.callbacks.drafts_get", ...)` — because `callbacks.py` does `from bot.routers._helpers import drafts_get`, and that's the name the handler resolves.
3. **Use `AsyncMock` for anything awaited.** Use `MagicMock` for sync.
4. **Keep tests <3s total.** No network, no real Redis, no real files.
