"""Unit tests for OneDrive approver capability helpers in webhook/bot/users.py."""
from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest


# ── get_onedrive_approver_ids ──

def test_get_approver_ids_empty_env(monkeypatch):
    monkeypatch.delenv("ONEDRIVE_APPROVER_IDS", raising=False)
    from bot.users import get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()
    assert get_onedrive_approver_ids() == []


def test_get_approver_ids_unset_env_returns_empty(monkeypatch):
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "")
    from bot.users import get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()
    assert get_onedrive_approver_ids() == []


def test_get_approver_ids_single(monkeypatch):
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "123")
    from bot.users import get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()
    assert get_onedrive_approver_ids() == [123]


def test_get_approver_ids_csv(monkeypatch):
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "123,456,789")
    from bot.users import get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()
    assert get_onedrive_approver_ids() == [123, 456, 789]


def test_get_approver_ids_with_whitespace(monkeypatch):
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", " 123 , 456 ,789 ")
    from bot.users import get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()
    assert get_onedrive_approver_ids() == [123, 456, 789]


def test_get_approver_ids_skips_malformed(monkeypatch, caplog):
    import logging
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "123,abc,456,,xyz")
    from bot.users import get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()
    with caplog.at_level(logging.WARNING):
        result = get_onedrive_approver_ids()
    assert result == [123, 456]
    # At least one warning logged about the malformed values
    assert any("approver" in r.message.lower() for r in caplog.records)


def test_get_approver_ids_caches_result(monkeypatch):
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "111")
    from bot.users import get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()
    first = get_onedrive_approver_ids()
    # Mutate env after first call — cache should win
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "222")
    second = get_onedrive_approver_ids()
    assert first == second == [111]


# ── is_onedrive_approver ──

def test_is_onedrive_approver_chat_in_env(monkeypatch):
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "456")
    from bot.users import is_onedrive_approver, get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()
    assert is_onedrive_approver(456) is True


def test_is_onedrive_approver_chat_not_in_env(monkeypatch):
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "456")
    from bot.users import is_onedrive_approver, get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()
    assert is_onedrive_approver(999) is False


def test_is_onedrive_approver_admin_implicit(monkeypatch):
    """Admin always passes regardless of env var."""
    monkeypatch.delenv("ONEDRIVE_APPROVER_IDS", raising=False)
    from bot.users import is_onedrive_approver, get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()
    with patch("bot.users.is_admin", return_value=True):
        assert is_onedrive_approver(123) is True


def test_is_onedrive_approver_subscriber_in_env(monkeypatch):
    """Subscriber added to env still gets capability — orthogonal to role."""
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "555")
    from bot.users import is_onedrive_approver, get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()
    with patch("bot.users.is_admin", return_value=False):
        assert is_onedrive_approver(555) is True


# ── get_user_role unchanged (regression) ──

def test_get_user_role_unchanged_admin(monkeypatch):
    """Adding chat to env list must NOT change get_user_role for admin."""
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "999")
    from bot.users import get_user_role, get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()
    with patch("bot.users.is_admin", return_value=True):
        assert get_user_role(999) == "admin"


def test_get_user_role_unchanged_unknown_in_env(monkeypatch):
    """Chat in env but no Redis record + not admin → still 'unknown'.
       Capability is orthogonal to role; gating happens at the OneDrive router."""
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "777")
    from bot.users import get_user_role, get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()
    with patch("bot.users.is_admin", return_value=False), \
         patch("bot.users.get_user", return_value=None):
        assert get_user_role(777) == "unknown"


# ── format_user_label ──

def test_format_user_label_with_username():
    from bot.users import format_user_label
    user = MagicMock()
    user.username = "joao"
    user.first_name = "João"
    user.id = 12345
    assert format_user_label(user) == "@joao"


def test_format_user_label_no_username():
    from bot.users import format_user_label
    user = MagicMock()
    user.username = None
    user.first_name = "Maria"
    user.id = 67890
    assert format_user_label(user) == "Maria"


def test_format_user_label_no_username_no_name():
    from bot.users import format_user_label
    user = MagicMock()
    user.username = None
    user.first_name = None
    user.id = 1234567890
    # Final fallback: last 4 digits of chat_id, prefixed with "Usuário"
    label = format_user_label(user)
    assert label.startswith("Usuário ")
    assert "7890" in label


def test_format_user_label_empty_string_username_falls_through():
    from bot.users import format_user_label
    user = MagicMock()
    user.username = ""
    user.first_name = "Carlos"
    user.id = 1
    assert format_user_label(user) == "Carlos"
