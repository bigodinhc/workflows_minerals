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


# ── Write tests ──

class FakeWelcomeRecorder:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail
    def __call__(self, phone_uazapi: str):
        self.calls.append(phone_uazapi)
        if self.fail:
            raise RuntimeError("uazapi send failed")


def test_add_happy_path_sends_welcome_then_inserts(fake_client):
    # 1st query: dup pre-check (empty), 2nd: insert (returns row)
    dup_q = FakeQuery([])
    insert_q = FakeQuery([_row(name="Carol", phone_uazapi="5511900000001")])

    fake_client.table.side_effect = [dup_q, insert_q]

    welcome = FakeWelcomeRecorder()
    repo = ContactsRepo(client=fake_client)

    contact = repo.add("Carol", "+55 11 90000-0001", send_welcome=welcome)

    assert contact.name == "Carol"
    assert contact.phone_uazapi == "5511900000001"
    assert welcome.calls == ["5511900000001"]

    # Insert query must be an insert with status=ativo
    op, args, _ = insert_q.calls[0]
    assert op == "insert"
    payload = args[0]
    assert payload["name"] == "Carol"
    assert payload["phone_uazapi"] == "5511900000001"
    assert payload["status"] == "ativo"


def test_add_invalid_phone_never_sends_welcome(fake_client):
    welcome = FakeWelcomeRecorder()
    repo = ContactsRepo(client=fake_client)

    with pytest.raises(InvalidPhoneError):
        repo.add("Carol", "abc", send_welcome=welcome)

    assert welcome.calls == []
    fake_client.table.assert_not_called()


def test_add_duplicate_pre_check_raises_and_skips_send(fake_client):
    dup_q = FakeQuery([_row(name="Alice Existing")])
    _set_next_query(fake_client, dup_q)

    welcome = FakeWelcomeRecorder()
    repo = ContactsRepo(client=fake_client)

    with pytest.raises(ContactAlreadyExistsError) as exc_info:
        repo.add("Alice", "+5511987654321", send_welcome=welcome)

    assert exc_info.value.existing.name == "Alice Existing"
    assert welcome.calls == []


def test_add_welcome_failure_rolls_back_insert(fake_client):
    dup_q = FakeQuery([])
    _set_next_query(fake_client, dup_q)

    welcome = FakeWelcomeRecorder(fail=True)
    repo = ContactsRepo(client=fake_client)

    with pytest.raises(RuntimeError, match="welcome send failed"):
        repo.add("Carol", "+5511900000002", send_welcome=welcome)

    # Only the pre-check should have been issued — no insert.
    assert len(fake_client._queries) == 1
    assert "insert" not in [c[0] for c in dup_q.calls]


def test_add_send_welcome_called_before_insert(fake_client):
    """Ordering guarantee: welcome send must precede DB insert."""
    call_order = []

    dup_q = FakeQuery([])
    insert_q = FakeQuery([_row()])
    fake_client.table.side_effect = [dup_q, insert_q]

    def welcome(p):
        call_order.append("welcome")

    orig_insert = insert_q.insert
    def tracked_insert(*a, **kw):
        call_order.append("insert")
        return orig_insert(*a, **kw)
    insert_q.insert = tracked_insert

    repo = ContactsRepo(client=fake_client)
    repo.add("Alice", "+5511987654321", send_welcome=welcome)

    assert call_order == ["welcome", "insert"]


def test_toggle_flips_ativo_to_inativo(fake_client):
    get_q = FakeQuery([_row(status="ativo")])
    update_q = FakeQuery([_row(status="inativo")])
    fake_client.table.side_effect = [get_q, update_q]

    repo = ContactsRepo(client=fake_client)
    updated = repo.toggle("+5511987654321")

    assert updated.status == "inativo"
    op, args, _ = update_q.calls[0]
    assert op == "update"
    assert args[0] == {"status": "inativo"}


def test_toggle_flips_inativo_to_ativo(fake_client):
    get_q = FakeQuery([_row(status="inativo")])
    update_q = FakeQuery([_row(status="ativo")])
    fake_client.table.side_effect = [get_q, update_q]

    repo = ContactsRepo(client=fake_client)
    updated = repo.toggle("+5511987654321")

    assert updated.status == "ativo"


def test_toggle_raises_on_missing_phone(fake_client):
    get_q = FakeQuery([])
    _set_next_query(fake_client, get_q)

    repo = ContactsRepo(client=fake_client)
    with pytest.raises(ContactNotFoundError):
        repo.toggle("+5511987654321")


def test_bulk_set_status_no_search_affects_all(fake_client):
    update_q = FakeQuery([_row(), _row(), _row()])
    _set_next_query(fake_client, update_q)

    repo = ContactsRepo(client=fake_client)
    count = repo.bulk_set_status("inativo")

    assert count == 3
    ops = [c[0] for c in update_q.calls]
    assert "update" in ops
    assert "ilike" not in ops


def test_bulk_set_status_with_search_filters(fake_client):
    update_q = FakeQuery([_row(name="Joao")])
    _set_next_query(fake_client, update_q)

    repo = ContactsRepo(client=fake_client)
    count = repo.bulk_set_status("ativo", search="joao")

    assert count == 1
    assert ("ilike", ("name", "%joao%"), {}) in update_q.calls


def test_bulk_set_status_rejects_invalid_status(fake_client):
    repo = ContactsRepo(client=fake_client)
    with pytest.raises(ValueError, match="invalid status"):
        repo.bulk_set_status("banido")
