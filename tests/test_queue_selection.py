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


def test_get_page_defaults_to_1_when_absent(fake_redis):
    from webhook.queue_selection import get_page
    assert get_page(42) == 1


def test_enter_mode_initializes_page_to_1(fake_redis):
    from webhook.queue_selection import enter_mode, get_page
    enter_mode(42)
    assert get_page(42) == 1


def test_set_page_persists(fake_redis):
    from webhook.queue_selection import enter_mode, set_page, get_page
    enter_mode(42)
    set_page(42, 3)
    assert get_page(42) == 3


def test_exit_mode_clears_page(fake_redis):
    from webhook.queue_selection import enter_mode, set_page, exit_mode, get_page
    enter_mode(42)
    set_page(42, 5)
    exit_mode(42)
    assert get_page(42) == 1  # default after deletion


def test_set_page_refreshes_all_ttls(fake_redis):
    from webhook.queue_selection import enter_mode, set_page, _TTL_SECONDS
    enter_mode(42)
    # Burn TTLs
    fake_redis.expire("bot:queue_mode:42", 5)
    fake_redis.expire("bot:queue_selected:42", 5)
    set_page(42, 2)
    # All three keys should have fresh TTL close to full
    for key in ("bot:queue_mode:42", "bot:queue_selected:42", "bot:queue_page:42"):
        ttl = fake_redis.ttl(key)
        # bot:queue_selected:42 may not exist if no items selected — fakeredis returns -2
        if ttl == -2:
            continue
        assert ttl > 5


def test_get_page_handles_garbage_value(fake_redis):
    from webhook.queue_selection import get_page
    fake_redis.set("bot:queue_page:42", "not-a-number")
    assert get_page(42) == 1


def test_toggle_refreshes_page_ttl(fake_redis):
    from webhook.queue_selection import enter_mode, toggle, _TTL_SECONDS
    enter_mode(42)
    fake_redis.expire("bot:queue_page:42", 5)
    toggle(42, "a")
    ttl = fake_redis.ttl("bot:queue_page:42")
    assert ttl > 5
