"""Unit tests for ContactsRepo list support (list_lists, list_by_list_code)."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock
from execution.integrations.contacts_repo import (
    ContactsRepo, ContactList,
)


class _FakeSupabaseTable:
    """Minimal chainable fake for supabase-py table builder.

    Supports only .select/.eq/.in_/.order/.execute — no column projection,
    no .count. Sufficient for list_lists and list_by_list_code.
    """
    def __init__(self, data):
        self._data = list(data)

    def select(self, *args, **kwargs):
        return self

    def eq(self, col, val):
        self._data = [r for r in self._data if r.get(col) == val]
        return self

    def in_(self, col, vals):
        self._data = [r for r in self._data if r.get(col) in vals]
        return self

    def order(self, *args, **kwargs):
        return self

    def execute(self):
        resp = MagicMock()
        resp.data = self._data
        return resp


class _FakeSupabase:
    def __init__(self, tables):
        self._tables = tables

    def table(self, name):
        return _FakeSupabaseTable(list(self._tables.get(name, [])))


@pytest.fixture
def _rows():
    # Isolated per-test dict of table rows.
    return {
        "contact_lists": [
            {"code": "minerals_report", "label": "Minerals Report", "description": None,
             "created_at": "2026-04-22T00:00:00Z"},
            {"code": "solid_fuels",     "label": "Solid Fuels",     "description": None,
             "created_at": "2026-04-22T00:00:00Z"},
            {"code": "time_interno",    "label": "Time Interno",    "description": None,
             "created_at": "2026-04-22T00:00:00Z"},
        ],
        "contact_list_members": [
            {"list_code": "minerals_report", "contact_phone": "5511111111111"},
            {"list_code": "minerals_report", "contact_phone": "5511222222222"},
            {"list_code": "solid_fuels",     "contact_phone": "5511111111111"},
        ],
        "contacts": [
            {"id": "a", "name": "Alice", "phone_raw": "+55 11 11111-1111",
             "phone_uazapi": "5511111111111", "status": "ativo",
             "created_at": "2026-04-01T00:00:00Z", "updated_at": "2026-04-01T00:00:00Z"},
            {"id": "b", "name": "Bob",   "phone_raw": "+55 11 22222-2222",
             "phone_uazapi": "5511222222222", "status": "ativo",
             "created_at": "2026-04-01T00:00:00Z", "updated_at": "2026-04-01T00:00:00Z"},
            {"id": "c", "name": "Carol", "phone_raw": "+55 11 33333-3333",
             "phone_uazapi": "5511333333333", "status": "inativo",
             "created_at": "2026-04-01T00:00:00Z", "updated_at": "2026-04-01T00:00:00Z"},
        ],
    }


@pytest.fixture
def fake_sb(_rows):
    return _FakeSupabase(_rows)


def test_contact_list_dataclass_is_frozen():
    import dataclasses
    cl = ContactList(code="x", label="X", member_count=0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        cl.code = "y"  # type: ignore


def test_list_lists_returns_all_with_member_count(fake_sb):
    repo = ContactsRepo(client=fake_sb)
    lists = repo.list_lists()
    by_code = {l.code: l for l in lists}
    assert set(by_code.keys()) == {"minerals_report", "solid_fuels", "time_interno"}
    assert by_code["minerals_report"].member_count == 2
    assert by_code["solid_fuels"].member_count == 1
    assert by_code["time_interno"].member_count == 0


def test_list_lists_returns_results_ordered_by_code(fake_sb):
    repo = ContactsRepo(client=fake_sb)
    lists = repo.list_lists()
    codes = [l.code for l in lists]
    assert codes == sorted(codes)


def test_list_by_list_code_returns_active_members_only(fake_sb):
    repo = ContactsRepo(client=fake_sb)
    members = repo.list_by_list_code("minerals_report")
    phones = {c.phone_uazapi for c in members}
    assert phones == {"5511111111111", "5511222222222"}
    for c in members:
        assert c.status == "ativo"


def test_list_by_list_code_excludes_inactive(_rows):
    # Add Carol (inativo) to minerals_report membership.
    _rows["contact_list_members"].append(
        {"list_code": "minerals_report", "contact_phone": "5511333333333"}
    )
    repo = ContactsRepo(client=_FakeSupabase(_rows))
    members = repo.list_by_list_code("minerals_report")
    phones = {c.phone_uazapi for c in members}
    assert "5511333333333" not in phones
    assert len(members) == 2


def test_list_by_list_code_unknown_list_returns_empty(fake_sb):
    repo = ContactsRepo(client=fake_sb)
    assert repo.list_by_list_code("nonexistent") == []


def test_list_lists_only_counts_active_members(_rows):
    # Bob becomes inativo; minerals_report should only count Alice.
    for c in _rows["contacts"]:
        if c["phone_uazapi"] == "5511222222222":
            c["status"] = "inativo"
    repo = ContactsRepo(client=_FakeSupabase(_rows))
    lists = repo.list_lists()
    by_code = {l.code: l for l in lists}
    assert by_code["minerals_report"].member_count == 1
