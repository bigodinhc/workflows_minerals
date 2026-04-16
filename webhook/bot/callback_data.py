"""CallbackData factory definitions.

Replace manual string parsing (callback_data.split(':', 1)) with typed
factories that serialize/deserialize automatically.
"""

from aiogram.filters.callback_data import CallbackData


class CurateAction(CallbackData, prefix="curate"):
    action: str  # archive, reject, pipeline
    item_id: str


class DraftAction(CallbackData, prefix="draft"):
    action: str  # approve, test_approve, adjust, reject
    draft_id: str


class MenuAction(CallbackData, prefix="menu"):
    target: str  # reports, queue, history, rejections, stats, status, etc.


class ReportType(CallbackData, prefix="rpt_type"):
    report_type: str


class ReportYear(CallbackData, prefix="rpt_year"):
    report_type: str
    year: int


class ReportMonth(CallbackData, prefix="rpt_month"):
    report_type: str
    year: int
    month: int


class ReportDownload(CallbackData, prefix="report_dl"):
    report_id: str


class ReportBack(CallbackData, prefix="rpt_back"):
    target: str  # types, type:<name>, years:<name>, year:<name>:<year>


class ReportYears(CallbackData, prefix="rpt_years"):
    report_type: str


class QueuePage(CallbackData, prefix="queue_page"):
    page: int


class QueueOpen(CallbackData, prefix="queue_open"):
    item_id: str


class ContactToggle(CallbackData, prefix="tgl"):
    phone: str


class ContactPage(CallbackData, prefix="pg"):
    page: int
    search: str = ""


class WorkflowRun(CallbackData, prefix="wf_run"):
    workflow_id: str


class WorkflowList(CallbackData, prefix="wf"):
    action: str  # list, back_menu
