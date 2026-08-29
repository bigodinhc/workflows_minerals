"""PostgREST caps every response at the server's max-rows and does it silently:
a truncated page is indistinguishable from a complete one. These tests pin the
paging that keeps /reports honest as the table grows past that cap.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import reports_nav


# ─── Fake PostgREST builder ──────────────────────────────────────────────────


class _FakeTable:
    """The builder chain, including the server-side row cap.

    `cap` is the server's max-rows: it clips every response, whatever window
    the client asked for. That clipping is the whole bug under test, so the
    fake has to reproduce it rather than hand back everything.
    """

    def __init__(self, rows, cap, calls):
        self._rows = rows
        self._cap = cap
        self._calls = calls
        self._predicates = []
        self._orders = []
        self._window = None
        self._limit = None

    def select(self, *_columns, **_kwargs):
        return self

    def eq(self, column, value):
        self._predicates.append(lambda r: r[column] == value)
        return self

    def gte(self, column, value):
        self._predicates.append(lambda r: r[column] >= value)
        return self

    def lte(self, column, value):
        self._predicates.append(lambda r: r[column] <= value)
        return self

    def lt(self, column, value):
        self._predicates.append(lambda r: r[column] < value)
        return self

    def order(self, column, desc=False):
        self._orders.append((column, desc))
        return self

    def limit(self, n):
        self._limit = n
        return self

    def range(self, start, end):
        self._window = (start, end)
        return self

    def execute(self):
        rows = [r for r in self._rows if all(p(r) for p in self._predicates)]
        # Stable sorts applied last-key-first give the multi-key ordering
        # PostgREST would produce for chained .order() calls.
        for column, desc in reversed(self._orders):
            rows = sorted(rows, key=lambda r: r[column], reverse=desc)
        if self._window:
            start, end = self._window
            rows = rows[start:end + 1]
        if self._limit is not None:
            rows = rows[:self._limit]
        rows = rows[:self._cap]
        self._calls.append({"window": self._window, "orders": list(self._orders), "n": len(rows)})
        return SimpleNamespace(data=rows)


class _FakeSupabase:
    def __init__(self, rows, cap):
        self._rows = rows
        self._cap = cap
        self.calls = []

    def table(self, _name):
        return _FakeTable(self._rows, self._cap, self.calls)


def _row(rid, date_key, report_type="Market Reports"):
    return {
        "id": rid,
        "date_key": date_key,
        "report_type": report_type,
        "report_name": f"Report {rid}",
    }


# Ids ascend with insertion order, the way the real table grew: the daily flow
# wrote 2026 first, the backfills appended older years afterwards. So the
# oldest cover dates carry the *highest* ids and sit last — past the cap.
def _dataset():
    rows = [_row(i, f"2026-01-{i:02d}") for i in range(1, 13)]           # ids 1-12
    rows += [_row(i, f"2025-06-{i - 12:02d}") for i in range(13, 23)]     # ids 13-22
    rows += [_row(i, f"2024-03-{i - 22:02d}") for i in range(23, 28)]     # ids 23-27
    # A different report type, in a year no Market Report covers: it appears in
    # the year list only if the report_type filter got dropped.
    rows += [_row(99, "2019-01-01", report_type="Research Reports")]
    return rows


@pytest.fixture
def fake_sb(monkeypatch):
    def _install(rows, cap):
        sb = _FakeSupabase(rows, cap)
        monkeypatch.setattr(reports_nav, "_supabase_client", sb)
        return sb
    return _install


@pytest.fixture
def captured_bot(monkeypatch):
    bot = AsyncMock()
    monkeypatch.setattr(reports_nav, "get_bot", lambda: bot)
    return bot


def _keyboard(bot):
    return bot.edit_message_text.await_args.kwargs["reply_markup"]["inline_keyboard"]


# ─── The years screen ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_years_include_a_year_that_only_exists_past_the_first_page(fake_sb, captured_bot):
    # cap=10 vs 27 matching rows: 2024 lives at offsets 20-26, invisible to a
    # single unbounded select.
    fake_sb(_dataset(), cap=10)

    await reports_nav.reports_show_years(1, 2, "Market Reports")

    labels = [row[0]["text"] for row in _keyboard(captured_bot)]
    assert labels == ["2026", "2025", "2024", "⬅ Voltar"]


@pytest.mark.asyncio
async def test_years_exclude_other_report_types(fake_sb, captured_bot):
    fake_sb(_dataset(), cap=10)

    await reports_nav.reports_show_years(1, 2, "Market Reports")

    labels = [row[0]["text"] for row in _keyboard(captured_bot)]
    assert "2019" not in labels


@pytest.mark.asyncio
async def test_years_page_with_a_deterministic_order(fake_sb, captured_bot):
    # Paging without a total order lets the server return one row on two pages
    # and another on none. Every page request must carry an ordering column.
    sb = fake_sb(_dataset(), cap=10)

    await reports_nav.reports_show_years(1, 2, "Market Reports")

    assert sb.calls, "no query was issued"
    assert all(call["orders"] for call in sb.calls)
    assert all(call["window"] is not None for call in sb.calls)


# ─── The months screen ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_month_counts_span_pages(fake_sb, captured_bot):
    rows = (
        [_row(i, f"2025-01-{i:02d}") for i in range(1, 13)]
        + [_row(i, f"2025-02-{i - 12:02d}") for i in range(13, 21)]
        + [_row(i, f"2025-03-{i - 20:02d}") for i in range(21, 26)]
        # Same year, different type: it inflates Janeiro if the filter is lost.
        + [_row(90, "2025-01-31", report_type="Research Reports")]
    )
    fake_sb(rows, cap=7)

    await reports_nav.reports_show_months(1, 2, "Market Reports", 2025)

    labels = [row[0]["text"] for row in _keyboard(captured_bot)]
    assert labels == ["Março (5)", "Fevereiro (8)", "Janeiro (12)", "⬅ Voltar"]


# ─── The paging helper itself ────────────────────────────────────────────────


def test_select_all_returns_every_row_when_the_cap_is_below_the_page_size():
    # The server's cap is not something the client is told. Stopping on
    # "shorter than the page I asked for" would return 700 of 2500 here.
    rows = [_row(i, "2025-01-01") for i in range(2500)]
    sb = _FakeSupabase(rows, cap=700)

    got = reports_nav._select_all(lambda: sb.table("platts_reports").select("id").order("id"))

    assert [r["id"] for r in got] == list(range(2500))


def test_select_all_stops_on_an_empty_page():
    rows = [_row(i, "2025-01-01") for i in range(5)]
    sb = _FakeSupabase(rows, cap=700)

    got = reports_nav._select_all(lambda: sb.table("platts_reports").select("id").order("id"))

    assert len(got) == 5
    assert sb.calls[-1]["n"] == 0
    assert len(sb.calls) == 2


# ─── The bounded reads, which page-free but must keep their shape ────────────


@pytest.mark.asyncio
async def test_latest_lists_the_ten_newest_first(fake_sb, captured_bot):
    rows = [_row(i, f"2025-01-{i:02d}") for i in range(1, 16)]
    fake_sb(rows, cap=1000)

    await reports_nav.reports_show_latest(1, 2, "Market Reports")

    labels = [row[0]["text"] for row in _keyboard(captured_bot)]
    assert labels[0] == "Report 15 — 2025-01-15"
    assert labels[9] == "Report 6 — 2025-01-06"
    assert len(labels) == 11  # 10 reports + the "ver por data / voltar" row


@pytest.mark.asyncio
async def test_month_list_covers_only_that_month(fake_sb, captured_bot):
    rows = (
        [_row(1, "2025-02-28")]
        + [_row(2, "2025-03-05")]
        + [_row(3, "2025-03-20")]
        + [_row(4, "2025-04-01")]
    )
    fake_sb(rows, cap=1000)

    await reports_nav.reports_show_month_list(1, 2, "Market Reports", 2025, 3)

    labels = [row[0]["text"] for row in _keyboard(captured_bot)]
    assert labels == ["Report 3 — 20/03", "Report 2 — 05/03", "⬅ Voltar"]
