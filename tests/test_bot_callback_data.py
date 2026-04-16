"""Tests for CallbackData factory serialization/deserialization."""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

import pytest
from bot.callback_data import (
    CurateAction, DraftAction, MenuAction,
    ReportType, ReportYear, ReportMonth, ReportDownload, ReportBack, ReportYears,
    QueuePage, QueueOpen,
    ContactToggle, ContactPage,
    WorkflowRun, WorkflowList,
)


def test_curate_action_pack_unpack():
    cb = CurateAction(action="archive", item_id="abc123")
    packed = cb.pack()
    assert packed.startswith("curate:")
    parsed = CurateAction.unpack(packed)
    assert parsed.action == "archive"
    assert parsed.item_id == "abc123"


def test_draft_action_pack_unpack():
    cb = DraftAction(action="approve", draft_id="news_12345")
    packed = cb.pack()
    assert packed.startswith("draft:")
    parsed = DraftAction.unpack(packed)
    assert parsed.action == "approve"
    assert parsed.draft_id == "news_12345"


def test_menu_action_pack_unpack():
    cb = MenuAction(target="reports")
    packed = cb.pack()
    assert packed.startswith("menu:")
    parsed = MenuAction.unpack(packed)
    assert parsed.target == "reports"


def test_report_type_pack_unpack():
    cb = ReportType(report_type="Market Reports")
    packed = cb.pack()
    parsed = ReportType.unpack(packed)
    assert parsed.report_type == "Market Reports"


def test_report_year_pack_unpack():
    cb = ReportYear(report_type="Market Reports", year=2026)
    packed = cb.pack()
    parsed = ReportYear.unpack(packed)
    assert parsed.report_type == "Market Reports"
    assert parsed.year == 2026


def test_report_month_pack_unpack():
    cb = ReportMonth(report_type="Research Reports", year=2025, month=11)
    packed = cb.pack()
    parsed = ReportMonth.unpack(packed)
    assert parsed.report_type == "Research Reports"
    assert parsed.year == 2025
    assert parsed.month == 11


def test_report_download_pack_unpack():
    cb = ReportDownload(report_id="uuid-abc")
    packed = cb.pack()
    parsed = ReportDownload.unpack(packed)
    assert parsed.report_id == "uuid-abc"


def test_report_back_pack_unpack():
    cb = ReportBack(target="types")
    packed = cb.pack()
    parsed = ReportBack.unpack(packed)
    assert parsed.target == "types"


def test_report_years_pack_unpack():
    cb = ReportYears(report_type="Market Reports")
    packed = cb.pack()
    parsed = ReportYears.unpack(packed)
    assert parsed.report_type == "Market Reports"


def test_queue_page_pack_unpack():
    cb = QueuePage(page=3)
    packed = cb.pack()
    parsed = QueuePage.unpack(packed)
    assert parsed.page == 3


def test_queue_open_pack_unpack():
    cb = QueueOpen(item_id="platts-xyz")
    packed = cb.pack()
    parsed = QueueOpen.unpack(packed)
    assert parsed.item_id == "platts-xyz"


def test_contact_toggle_pack_unpack():
    cb = ContactToggle(phone="5511999999999")
    packed = cb.pack()
    parsed = ContactToggle.unpack(packed)
    assert parsed.phone == "5511999999999"


def test_contact_page_pack_unpack():
    cb = ContactPage(page=2, search="joao")
    packed = cb.pack()
    parsed = ContactPage.unpack(packed)
    assert parsed.page == 2
    assert parsed.search == "joao"


def test_contact_page_no_search():
    cb = ContactPage(page=1)
    packed = cb.pack()
    parsed = ContactPage.unpack(packed)
    assert parsed.page == 1
    assert parsed.search == ""


def test_workflow_run_pack_unpack():
    cb = WorkflowRun(workflow_id="morning_check.yml")
    packed = cb.pack()
    parsed = WorkflowRun.unpack(packed)
    assert parsed.workflow_id == "morning_check.yml"


def test_workflow_list_pack_unpack():
    cb = WorkflowList(action="list")
    packed = cb.pack()
    parsed = WorkflowList.unpack(packed)
    assert parsed.action == "list"
