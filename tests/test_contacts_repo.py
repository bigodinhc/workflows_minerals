"""Unit tests for ContactsRepo using a fake Supabase client."""
from datetime import datetime, timezone
from unittest.mock import MagicMock
import pytest

from execution.integrations.contacts_repo import (
    Contact, ContactsRepo, ContactNotFoundError,
    ContactAlreadyExistsError, InvalidPhoneError,
)


def _row(**overrides):
    base = {
        "id": "00000000-0000-0000-0000-000000000001",
        "name": "Alice",
        "phone_raw": "+5511987654321",
        "phone_uazapi": "5511987654321",
        "status": "ativo",
        "created_at": "2026-04-22T10:00:00+00:00",
        "updated_at": "2026-04-22T10:00:00+00:00",
    }
    base.update(overrides)
    return base


class FakeQuery:
    """Minimal chainable builder: mirrors supabase-py's PostgrestBuilder."""
    def __init__(self, data, count=None):
        self._data = data
        self._count = count
        self.calls = []

    def select(self, *a, **kw): self.calls.append(("select", a, kw)); return self
    def eq(self, *a, **kw):     self.calls.append(("eq", a, kw)); return self
    def ilike(self, *a, **kw):  self.calls.append(("ilike", a, kw)); return self
    def neq(self, *a, **kw):    self.calls.append(("neq", a, kw)); return self
    def order(self, *a, **kw):  self.calls.append(("order", a, kw)); return self
    def range(self, *a, **kw):  self.calls.append(("range", a, kw)); return self
    def limit(self, *a, **kw):  self.calls.append(("limit", a, kw)); return self
    def insert(self, *a, **kw): self.calls.append(("insert", a, kw)); return self
    def update(self, *a, **kw): self.calls.append(("update", a, kw)); return self
    def upsert(self, *a, **kw): self.calls.append(("upsert", a, kw)); return self

    def execute(self):
        r = MagicMock()
        r.data = self._data
        r.count = self._count
        return r


@pytest.fixture
def fake_client():
    client = MagicMock()
    client._queries = []
    return client


def _set_next_query(client, query: FakeQuery):
    """Configure client.table(...) to return `query` on the next call."""
    client.table.return_value = query
    client._queries.append(query)


def test_contact_is_active_true_for_ativo():
    c = Contact(
        id="x", name="A", phone_raw="+1", phone_uazapi="1",
        status="ativo", created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    assert c.is_active() is True


def test_contact_is_active_false_for_inativo():
    c = Contact(
        id="x", name="A", phone_raw="+1", phone_uazapi="1",
        status="inativo", created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    assert c.is_active() is False


def test_list_active_filters_by_status_and_orders(fake_client):
    q = FakeQuery([_row(), _row(name="Bob")])
    _set_next_query(fake_client, q)

    repo = ContactsRepo(client=fake_client)
    contacts = repo.list_active()

    assert len(contacts) == 2
    assert contacts[0].name == "Alice"
    ops = [c[0] for c in q.calls]
    assert ops == ["select", "eq", "order"]
    assert q.calls[1] == ("eq", ("status", "ativo"), {})
    assert q.calls[2] == ("order", ("created_at",), {"desc": True})


def test_list_all_no_search(fake_client):
    q = FakeQuery([_row() for _ in range(3)], count=3)
    _set_next_query(fake_client, q)

    repo = ContactsRepo(client=fake_client)
    contacts, total_pages = repo.list_all(page=1, per_page=10)

    assert len(contacts) == 3
    assert total_pages == 1
    ops = [c[0] for c in q.calls]
    assert "ilike" not in ops
    assert ("range", (0, 9), {}) in q.calls


def test_list_all_with_search_uses_ilike(fake_client):
    q = FakeQuery([_row(name="Joao")], count=1)
    _set_next_query(fake_client, q)

    repo = ContactsRepo(client=fake_client)
    contacts, _ = repo.list_all(search="joao", page=1, per_page=10)

    assert len(contacts) == 1
    assert ("ilike", ("name", "%joao%"), {}) in q.calls


def test_list_all_computes_total_pages(fake_client):
    q = FakeQuery([_row() for _ in range(10)], count=25)
    _set_next_query(fake_client, q)

    repo = ContactsRepo(client=fake_client)
    _, total_pages = repo.list_all(page=1, per_page=10)

    assert total_pages == 3  # ceil(25/10)


def test_list_all_pagination_page_3(fake_client):
    q = FakeQuery([_row()], count=25)
    _set_next_query(fake_client, q)

    repo = ContactsRepo(client=fake_client)
    repo.list_all(page=3, per_page=10)

    assert ("range", (20, 29), {}) in q.calls


def test_list_all_empty(fake_client):
    q = FakeQuery([], count=0)
    _set_next_query(fake_client, q)

    repo = ContactsRepo(client=fake_client)
    contacts, total_pages = repo.list_all()

    assert contacts == []
    assert total_pages == 0


def test_get_by_phone_normalizes_input(fake_client):
    q = FakeQuery([_row()])
    _set_next_query(fake_client, q)

    repo = ContactsRepo(client=fake_client)
    c = repo.get_by_phone("+55 (11) 98765-4321")

    assert c.phone_uazapi == "5511987654321"
    assert ("eq", ("phone_uazapi", "5511987654321"), {}) in q.calls


def test_get_by_phone_raises_when_missing(fake_client):
    q = FakeQuery([])
    _set_next_query(fake_client, q)

    repo = ContactsRepo(client=fake_client)
    with pytest.raises(ContactNotFoundError):
        repo.get_by_phone("+5511900000001")


def test_get_by_phone_invalid_input_raises_invalid_phone(fake_client):
    repo = ContactsRepo(client=fake_client)
    with pytest.raises(InvalidPhoneError):
        repo.get_by_phone("abc")
