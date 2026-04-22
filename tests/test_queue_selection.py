"""Tests for webhook.queue_selection — per-chat select-mode state in Redis."""
import pytest
import fakeredis


@pytest.fixture
def fake_redis(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    from execution.curation import redis_client
    monkeypatch.setattr(redis_client, "_get_client", lambda: fake)
    monkeypatch.setattr(redis_client, "_client", None)
    return fake


def test_mode_absent_by_default(fake_redis):
    from webhook.queue_selection import is_select_mode
    assert is_select_mode(42) is False


def test_enter_mode_sets_flag_and_empty_selection(fake_redis):
    from webhook.queue_selection import enter_mode, is_select_mode, get_selection
    enter_mode(42)
    assert is_select_mode(42) is True
    assert get_selection(42) == set()


def test_enter_mode_is_per_chat(fake_redis):
    from webhook.queue_selection import enter_mode, is_select_mode
    enter_mode(42)
    assert is_select_mode(99) is False


def test_toggle_adds_then_removes(fake_redis):
    from webhook.queue_selection import enter_mode, toggle, get_selection
    enter_mode(42)
    assert toggle(42, "a") is True       # now selected
    assert get_selection(42) == {"a"}
    assert toggle(42, "a") is False      # now unselected
    assert get_selection(42) == set()


def test_toggle_multiple_ids(fake_redis):
    from webhook.queue_selection import enter_mode, toggle, get_selection
    enter_mode(42)
    toggle(42, "a")
    toggle(42, "b")
    toggle(42, "c")
    assert get_selection(42) == {"a", "b", "c"}


def test_select_all_overwrites_existing(fake_redis):
    from webhook.queue_selection import enter_mode, toggle, select_all, get_selection
    enter_mode(42)
    toggle(42, "a")
    select_all(42, ["x", "y", "z"])
    assert get_selection(42) == {"x", "y", "z"}


def test_clear_empties_selection_but_keeps_mode(fake_redis):
    from webhook.queue_selection import enter_mode, toggle, clear, get_selection, is_select_mode
    enter_mode(42)
    toggle(42, "a")
    clear(42)
    assert get_selection(42) == set()
    assert is_select_mode(42) is True


def test_exit_mode_deletes_both_keys(fake_redis):
    from webhook.queue_selection import enter_mode, toggle, exit_mode, is_select_mode, get_selection
    enter_mode(42)
    toggle(42, "a")
    exit_mode(42)
    assert is_select_mode(42) is False
    assert get_selection(42) == set()


def test_enter_mode_sets_ttl(fake_redis):
    from webhook.queue_selection import enter_mode, _TTL_SECONDS
    enter_mode(42)
    mode_ttl = fake_redis.ttl("bot:queue_mode:42")
    assert 0 < mode_ttl <= _TTL_SECONDS


def test_toggle_refreshes_ttl(fake_redis):
    from webhook.queue_selection import enter_mode, toggle, _TTL_SECONDS
    enter_mode(42)
    # Simulate partial TTL burn
    fake_redis.expire("bot:queue_mode:42", 5)
    toggle(42, "a")
    mode_ttl = fake_redis.ttl("bot:queue_mode:42")
    assert mode_ttl > 5
    sel_ttl = fake_redis.ttl("bot:queue_selected:42")
    assert 0 < sel_ttl <= _TTL_SECONDS


def test_get_selection_returns_empty_when_mode_absent(fake_redis):
    from webhook.queue_selection import get_selection
    assert get_selection(42) == set()
