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
    flt: str = "t"  # "t" (todos) | "a" | "i" | "mr" | "sf"


class ContactPage(CallbackData, prefix="pg"):
    page: int
    search: str = ""
    flt: str = "t"


class ContactFilter(CallbackData, prefix="cf"):
    """Tap on a filter chip above the contact list."""
    value: str  # "t" | "a" | "i" | "mr" | "sf"


class WorkflowRun(CallbackData, prefix="wf_run"):
    workflow_id: str


class WorkflowList(CallbackData, prefix="wf"):
    action: str  # list, back_menu


class UserApproval(CallbackData, prefix="user_approve"):
    action: str  # approve, reject
    chat_id: int


class SubscriptionToggle(CallbackData, prefix="sub_toggle"):
    workflow: str  # morning_check, baltic_ingestion, etc.


class SubscriptionDone(CallbackData, prefix="sub_done"):
    pass


class OnboardingStart(CallbackData, prefix="onboard"):
    pass


class BroadcastConfirm(CallbackData, prefix="bcast"):
    action: str  # send, cancel
    draft_id: str = ""


class ContactBulk(CallbackData, prefix="bulk"):
    """First tap on bulk activate/deactivate. Shows confirmation prompt."""
    status: str       # 'ativo' | 'inativo'
    search: str = ""
    flt: str = "t"


class ContactBulkConfirm(CallbackData, prefix="bulkok"):
    """Second tap — user confirmed the bulk action."""
    status: str       # 'ativo' | 'inativo'
    search: str = ""
    flt: str = "t"


class ContactBulkCancel(CallbackData, prefix="bulkno"):
    """Cancel the pending bulk action."""
    pass


class QueueModeToggle(CallbackData, prefix="q_mode"):
    """Enter or exit select mode from the /queue header."""
    action: str  # 'enter' | 'exit'


class QueueSelToggle(CallbackData, prefix="q_sel"):
    """Toggle selection of a single item (only valid in select mode)."""
    item_id: str


class QueueSelAll(CallbackData, prefix="q_all"):
    """Select every staging item across all pages."""
    pass


class QueueSelNone(CallbackData, prefix="q_none"):
    """Clear the current selection (keeps select mode active)."""
    pass


class QueueBulkPrompt(CallbackData, prefix="q_bulk"):
    """First tap on archive/discard — shows confirmation."""
    action: str  # 'archive' | 'discard'


class QueueBulkConfirm(CallbackData, prefix="q_bulkok"):
    """User confirmed — execute the action on current selection."""
    action: str  # 'archive' | 'discard'


class QueueBulkCancel(CallbackData, prefix="q_bulkno"):
    """Cancel confirmation, return to select-mode view."""
    pass


class OneDriveApprove(CallbackData, prefix="od_ap"):
    """First click from approval card — admin picked a list (or '__all__')."""
    approval_id: str       # UUID of Redis approval:{uuid} key
    list_code: str         # contact_lists.code OR '__all__'


class OneDriveConfirm(CallbackData, prefix="od_cf"):
    """Second click — admin confirmed the envio on the confirmation screen."""
    approval_id: str
    list_code: str


class OneDriveDiscard(CallbackData, prefix="od_dc"):
    """Admin clicked Descartar on the approval card."""
    approval_id: str
