"""Tests for webhook/bot/users.py — Redis-backed user store."""
import sys
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

import fakeredis
import pytest


@pytest.fixture
def fake_redis():
    client = fakeredis.FakeRedis(decode_responses=True)
    with patch("bot.users._get_client", return_value=client):
        yield client


def test_create_pending_user(fake_redis):
    from bot.users import create_pending_user, get_user
    create_pending_user(chat_id=111, name="Joao", username="joaosilva")
    user = get_user(111)
    assert user is not None
    assert user["chat_id"] == 111
    assert user["name"] == "Joao"
    assert user["username"] == "joaosilva"
    assert user["role"] == "subscriber"
    assert user["status"] == "pending"
    assert all(user["subscriptions"][k] is True for k in user["subscriptions"])


def test_get_user_not_found(fake_redis):
    from bot.users import get_user
    assert get_user(999) is None


def test_approve_user(fake_redis):
    from bot.users import create_pending_user, approve_user, get_user
    create_pending_user(chat_id=222, name="Maria", username="maria")
    approve_user(222)
    user = get_user(222)
    assert user["status"] == "approved"
    assert user["approved_at"] is not None


def test_reject_user(fake_redis):
    from bot.users import create_pending_user, reject_user, get_user
    create_pending_user(chat_id=333, name="Pedro", username="pedro")
    reject_user(333)
    user = get_user(333)
    assert user["status"] == "rejected"


def test_get_subscribers_for_workflow(fake_redis):
    from bot.users import create_pending_user, approve_user, get_subscribers_for_workflow, toggle_subscription
    create_pending_user(chat_id=100, name="A", username="a")
    create_pending_user(chat_id=200, name="B", username="b")
    approve_user(100)
    approve_user(200)
    toggle_subscription(200, "morning_check")  # turn OFF
    subs = get_subscribers_for_workflow("morning_check")
    assert len(subs) == 1
    assert subs[0]["chat_id"] == 100


def test_toggle_subscription(fake_redis):
    from bot.users import create_pending_user, get_user, toggle_subscription
    create_pending_user(chat_id=444, name="Ana", username="ana")
    new_val = toggle_subscription(444, "morning_check")
    assert new_val is False  # was True, now False
    user = get_user(444)
    assert user["subscriptions"]["morning_check"] is False
    new_val = toggle_subscription(444, "morning_check")
    assert new_val is True  # toggled back


def test_is_admin(fake_redis):
    from bot.users import is_admin
    with patch("bot.users.ADMIN_CHAT_ID", 111):
        assert is_admin(111) is True
        assert is_admin(999) is False


def test_get_user_role(fake_redis):
    from bot.users import create_pending_user, approve_user, get_user_role
    assert get_user_role(999) == "unknown"
    create_pending_user(chat_id=555, name="X", username="x")
    assert get_user_role(555) == "pending"
    approve_user(555)
    assert get_user_role(555) == "subscriber"


def test_admin_role_from_env(fake_redis):
    from bot.users import get_user_role
    with patch("bot.users.ADMIN_CHAT_ID", 777):
        assert get_user_role(777) == "admin"


def test_list_pending_users(fake_redis):
    from bot.users import create_pending_user, list_pending_users
    create_pending_user(chat_id=10, name="P1", username="p1")
    create_pending_user(chat_id=20, name="P2", username="p2")
    pending = list_pending_users()
    assert len(pending) == 2
    ids = {u["chat_id"] for u in pending}
    assert ids == {10, 20}
