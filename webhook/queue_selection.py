"""Per-chat select-mode state for /queue bulk actions.

Uses the same Redis instance as the curation keyspace (via
execution.curation.redis_client._get_client). Two keys per chat:

- bot:queue_mode:{chat_id}      string, value "select" when active
- bot:queue_selected:{chat_id}  set of staging item ids

Both keys share a 10 minute TTL and are refreshed on every mutation.
Exiting the mode deletes both keys. The state is volatile by design —
bot restarts discard it.
"""
from __future__ import annotations

from execution.curation import redis_client

_TTL_SECONDS = 10 * 60
_MODE_VALUE = "select"


def _mode_key(chat_id: int) -> str:
    return f"bot:queue_mode:{chat_id}"


def _selected_key(chat_id: int) -> str:
    return f"bot:queue_selected:{chat_id}"


def _refresh_ttl(pipe, chat_id: int) -> None:
    pipe.expire(_mode_key(chat_id), _TTL_SECONDS)
    pipe.expire(_selected_key(chat_id), _TTL_SECONDS)


def is_select_mode(chat_id: int) -> bool:
    client = redis_client._get_client()
    return client.get(_mode_key(chat_id)) == _MODE_VALUE


def enter_mode(chat_id: int) -> None:
    client = redis_client._get_client()
    pipe = client.pipeline()
    pipe.set(_mode_key(chat_id), _MODE_VALUE, ex=_TTL_SECONDS)
    pipe.delete(_selected_key(chat_id))
    pipe.execute()


def exit_mode(chat_id: int) -> None:
    client = redis_client._get_client()
    pipe = client.pipeline()
    pipe.delete(_mode_key(chat_id))
    pipe.delete(_selected_key(chat_id))
    pipe.execute()


def get_selection(chat_id: int) -> set[str]:
    client = redis_client._get_client()
    members = client.smembers(_selected_key(chat_id))
    return set(members) if members else set()


def toggle(chat_id: int, item_id: str) -> bool:
    """Toggle item_id in the selection. Returns True if now selected."""
    client = redis_client._get_client()
    selected_key = _selected_key(chat_id)
    if client.sismember(selected_key, item_id):
        pipe = client.pipeline()
        pipe.srem(selected_key, item_id)
        _refresh_ttl(pipe, chat_id)
        pipe.execute()
        return False
    pipe = client.pipeline()
    pipe.sadd(selected_key, item_id)
    _refresh_ttl(pipe, chat_id)
    pipe.execute()
    return True


def select_all(chat_id: int, item_ids: list[str]) -> None:
    client = redis_client._get_client()
    selected_key = _selected_key(chat_id)
    pipe = client.pipeline()
    pipe.delete(selected_key)
    if item_ids:
        pipe.sadd(selected_key, *item_ids)
    _refresh_ttl(pipe, chat_id)
    pipe.execute()


def clear(chat_id: int) -> None:
    client = redis_client._get_client()
    pipe = client.pipeline()
    pipe.delete(_selected_key(chat_id))
    _refresh_ttl(pipe, chat_id)
    pipe.execute()
