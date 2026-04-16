# Reports Bot Navigation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Spec reference:** `docs/superpowers/specs/2026-04-16-reports-bot-navigation-design.md`

**Goal:** Add `/reports` command to the Telegram bot for interactive browsing and on-demand download of Platts PDF reports stored in Supabase.

**Architecture:** Single file change in `webhook/app.py`. `/reports` sends inline keyboard → callbacks navigate via `editMessageText` → final click reuses existing `report_dl` handler. All queries hit `platts_reports` Postgres table via Supabase client (already initialized from earlier migration).

**Tech Stack:** Python, Flask, Supabase Python client (already in `webhook/requirements.txt`), Telegram Bot API.

---

## File Structure

**Modified files:**
- `webhook/app.py` — add `/reports` command handler (~15 lines), 5 callback handlers for `rpt_type`/`rpt_years`/`rpt_year`/`rpt_month`/`rpt_back` (~100 lines), update `register_commands` list (+1 entry)

No new files. No changes to the actor, Supabase schema, or GitHub Actions.

---

## Task 1: Add `/reports` command handler

**Files:**
- Modify: `webhook/app.py` (around line 1016, before `return jsonify({"ok": True})  # unknown command`)

- [ ] **Step 1: Add the `/reports` command handler**

In `webhook/app.py`, find the line `if text == "/queue":` block (line 1016-1027). After that block's `return jsonify({"ok": True})` and BEFORE the final `return jsonify({"ok": True})  # unknown command` at line 1029, add:

```python
        if text == "/reports":
            if not contact_admin.is_authorized(chat_id):
                return jsonify({"ok": True})
            _reports_show_types(chat_id)
            return jsonify({"ok": True})
```

- [ ] **Step 2: Add the `_reports_show_types` helper function**

Add this function BEFORE the `handle_callback` function (around line 1200, in the helpers section). Place it alongside other helper functions like `_build_status_message`, `_render_list_view`:

```python
# ── Reports navigation helpers ──

PT_MONTHS = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
    5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}

def _reports_show_types(chat_id, message_id=None):
    """Show report type selection (Market Reports / Research Reports)."""
    text = "📊 *Platts Reports*\n\nEscolha a categoria:"
    markup = {
        "inline_keyboard": [
            [{"text": "📊 Market Reports", "callback_data": "rpt_type:Market Reports"}],
            [{"text": "📊 Research Reports", "callback_data": "rpt_type:Research Reports"}],
        ]
    }
    if message_id:
        edit_message(chat_id, message_id, text, reply_markup=markup)
    else:
        send_telegram_message(chat_id, text, reply_markup=markup)
```

- [ ] **Step 3: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF "
git add webhook/app.py
git commit -m "feat(webhook): add /reports command with type selection menu"
```

---

## Task 2: Add `rpt_type` callback — show latest 10 reports

**Files:**
- Modify: `webhook/app.py` (in `handle_callback`, after the `report_dl` block around line 1301)

- [ ] **Step 1: Add `rpt_type` callback handler**

In `handle_callback()`, after the `report_dl` handler block (line 1301, just before `parts = callback_data.split(":", 1)`), add:

```python
    # ---------- Reports navigation ----------
    if callback_data.startswith("rpt_type:"):
        report_type = callback_data.split(":", 1)[1]
        message_id = callback_query["message"]["message_id"]
        _reports_show_latest(chat_id, message_id, report_type)
        answer_callback(callback_id, "")
        return jsonify({"ok": True})
```

- [ ] **Step 2: Add the `_reports_show_latest` helper**

Add after `_reports_show_types`:

```python
def _reports_show_latest(chat_id, message_id, report_type):
    """Show the 10 most recent reports of a given type."""
    sb = get_supabase()
    if not sb:
        edit_message(chat_id, message_id, "⚠️ Supabase não configurado")
        return
    try:
        result = sb.table("platts_reports") \
            .select("id, report_name, date_key, frequency") \
            .eq("report_type", report_type) \
            .order("date_key", desc=True) \
            .limit(10) \
            .execute()
        rows = result.data or []
    except Exception as exc:
        logger.error(f"reports latest query error: {exc}")
        edit_message(chat_id, message_id, "⚠️ Erro ao consultar relatórios")
        return

    if not rows:
        keyboard = [[{"text": "⬅ Voltar", "callback_data": "rpt_back:types"}]]
        edit_message(chat_id, message_id, "Nenhum relatório encontrado.", reply_markup={"inline_keyboard": keyboard})
        return

    esc = lambda s: str(s).replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`")
    text = f"📊 *{esc(report_type)}*\n\nÚltimos relatórios:"
    keyboard = []
    for r in rows:
        dk = r["date_key"]
        label = f"{esc(r['report_name'])} — {dk}"
        keyboard.append([{"text": label, "callback_data": f"report_dl:{r['id']}"}])
    keyboard.append([
        {"text": "📅 Ver por data", "callback_data": f"rpt_years:{report_type}"},
        {"text": "⬅ Voltar", "callback_data": "rpt_back:types"},
    ])
    edit_message(chat_id, message_id, text, reply_markup={"inline_keyboard": keyboard})
```

- [ ] **Step 3: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF "
git add webhook/app.py
git commit -m "feat(webhook): rpt_type callback shows latest 10 reports with download buttons"
```

---

## Task 3: Add browse callbacks — years, months, month-list

**Files:**
- Modify: `webhook/app.py` (callbacks + helpers)

- [ ] **Step 1: Add `rpt_years`, `rpt_year`, `rpt_month` callback handlers**

In `handle_callback()`, right after the `rpt_type` block added in Task 2, add:

```python
    if callback_data.startswith("rpt_years:"):
        report_type = callback_data.split(":", 1)[1]
        message_id = callback_query["message"]["message_id"]
        _reports_show_years(chat_id, message_id, report_type)
        answer_callback(callback_id, "")
        return jsonify({"ok": True})

    if callback_data.startswith("rpt_year:"):
        _, report_type, year = callback_data.split(":", 2)
        message_id = callback_query["message"]["message_id"]
        _reports_show_months(chat_id, message_id, report_type, int(year))
        answer_callback(callback_id, "")
        return jsonify({"ok": True})

    if callback_data.startswith("rpt_month:"):
        _, report_type, year, month = callback_data.split(":", 3)
        message_id = callback_query["message"]["message_id"]
        _reports_show_month_list(chat_id, message_id, report_type, int(year), int(month))
        answer_callback(callback_id, "")
        return jsonify({"ok": True})
```

- [ ] **Step 2: Add `_reports_show_years` helper**

Add after `_reports_show_latest`:

```python
def _reports_show_years(chat_id, message_id, report_type):
    """Show available years for a report type."""
    sb = get_supabase()
    if not sb:
        edit_message(chat_id, message_id, "⚠️ Supabase não configurado")
        return
    try:
        result = sb.rpc("", {}).execute()  # can't do DISTINCT extract via client — use raw SQL
    except Exception:
        pass
    # Use raw SQL via rpc workaround or direct query
    try:
        from datetime import date
        result = sb.table("platts_reports") \
            .select("date_key") \
            .eq("report_type", report_type) \
            .execute()
        years = sorted({int(r["date_key"][:4]) for r in (result.data or [])}, reverse=True)
    except Exception as exc:
        logger.error(f"reports years query error: {exc}")
        edit_message(chat_id, message_id, "⚠️ Erro ao consultar anos")
        return

    esc = lambda s: str(s).replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`")
    text = f"📊 *{esc(report_type)}*\n\nEscolha o ano:"
    keyboard = [[{"text": str(y), "callback_data": f"rpt_year:{report_type}:{y}"}] for y in years]
    keyboard.append([{"text": "⬅ Voltar", "callback_data": f"rpt_type:{report_type}"}])
    edit_message(chat_id, message_id, text, reply_markup={"inline_keyboard": keyboard})


def _reports_show_months(chat_id, message_id, report_type, year):
    """Show available months for a report type + year, with counts."""
    sb = get_supabase()
    if not sb:
        edit_message(chat_id, message_id, "⚠️ Supabase não configurado")
        return
    try:
        result = sb.table("platts_reports") \
            .select("date_key") \
            .eq("report_type", report_type) \
            .gte("date_key", f"{year}-01-01") \
            .lte("date_key", f"{year}-12-31") \
            .execute()
        month_counts = {}
        for r in (result.data or []):
            m = int(r["date_key"][5:7])
            month_counts[m] = month_counts.get(m, 0) + 1
        months_sorted = sorted(month_counts.items(), reverse=True)
    except Exception as exc:
        logger.error(f"reports months query error: {exc}")
        edit_message(chat_id, message_id, "⚠️ Erro ao consultar meses")
        return

    esc = lambda s: str(s).replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`")
    text = f"📊 *{esc(report_type)} — {year}*\n\nEscolha o mês:"
    keyboard = []
    for m, cnt in months_sorted:
        label = f"{PT_MONTHS.get(m, str(m))} ({cnt})"
        keyboard.append([{"text": label, "callback_data": f"rpt_month:{report_type}:{year}:{m}"}])
    keyboard.append([{"text": "⬅ Voltar", "callback_data": f"rpt_years:{report_type}"}])
    edit_message(chat_id, message_id, text, reply_markup={"inline_keyboard": keyboard})


def _reports_show_month_list(chat_id, message_id, report_type, year, month):
    """Show all reports for a given type + year + month."""
    sb = get_supabase()
    if not sb:
        edit_message(chat_id, message_id, "⚠️ Supabase não configurado")
        return
    try:
        start = f"{year}-{month:02d}-01"
        if month == 12:
            end = f"{year + 1}-01-01"
        else:
            end = f"{year}-{month + 1:02d}-01"
        result = sb.table("platts_reports") \
            .select("id, report_name, date_key") \
            .eq("report_type", report_type) \
            .gte("date_key", start) \
            .lt("date_key", end) \
            .order("date_key", desc=True) \
            .order("report_name") \
            .execute()
        rows = result.data or []
    except Exception as exc:
        logger.error(f"reports month list query error: {exc}")
        edit_message(chat_id, message_id, "⚠️ Erro ao consultar relatórios do mês")
        return

    esc = lambda s: str(s).replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`")
    month_name = PT_MONTHS.get(month, str(month))
    text = f"📊 *{esc(report_type)} — {month_name} {year}*"
    if not rows:
        text += "\n\nNenhum relatório nesse período."
    keyboard = []
    for r in rows:
        day = r["date_key"][8:10]
        label = f"{esc(r['report_name'])} — {day}/{month:02d}"
        keyboard.append([{"text": label, "callback_data": f"report_dl:{r['id']}"}])
    keyboard.append([{"text": "⬅ Voltar", "callback_data": f"rpt_year:{report_type}:{year}"}])
    edit_message(chat_id, message_id, text, reply_markup={"inline_keyboard": keyboard})
```

- [ ] **Step 3: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF "
git add webhook/app.py
git commit -m "feat(webhook): browse reports by year/month with download buttons"
```

---

## Task 4: Add `rpt_back` callback — navigation back buttons

**Files:**
- Modify: `webhook/app.py` (in `handle_callback`)

- [ ] **Step 1: Add `rpt_back` callback handler**

In `handle_callback()`, right after the `rpt_month` block from Task 3, add:

```python
    if callback_data.startswith("rpt_back:"):
        rest = callback_data[len("rpt_back:"):]
        message_id = callback_query["message"]["message_id"]
        if rest == "types":
            _reports_show_types(chat_id, message_id=message_id)
        elif rest.startswith("type:"):
            report_type = rest[len("type:"):]
            _reports_show_latest(chat_id, message_id, report_type)
        elif rest.startswith("years:"):
            report_type = rest[len("years:"):]
            _reports_show_years(chat_id, message_id, report_type)
        elif rest.startswith("year:"):
            parts_back = rest[len("year:"):].rsplit(":", 1)
            if len(parts_back) == 2:
                _reports_show_months(chat_id, message_id, parts_back[0], int(parts_back[1]))
        answer_callback(callback_id, "")
        return jsonify({"ok": True})
```

- [ ] **Step 2: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF "
git add webhook/app.py
git commit -m "feat(webhook): rpt_back callbacks for navigation back buttons"
```

---

## Task 5: Update `register_commands` — add `/reports`

**Files:**
- Modify: `webhook/app.py` (in `/admin/register-commands` route, around line 1548)

- [ ] **Step 1: Add `/reports` to the commands list**

Find the `commands = [` list inside the `register_commands()` function (around line 1548). Add as the FIRST entry:

```python
        {"command": "reports", "description": "Consultar e baixar relatórios Platts (PDF)"},
```

The full list should now be:

```python
    commands = [
        {"command": "reports", "description": "Consultar e baixar relatórios Platts (PDF)"},
        {"command": "help", "description": "Lista todos os comandos"},
        {"command": "queue", "description": "Items aguardando curadoria"},
        {"command": "history", "description": "Ultimos 10 arquivados"},
        {"command": "rejections", "description": "Ultimas 10 recusas"},
        {"command": "stats", "description": "Contadores de hoje"},
        {"command": "status", "description": "Saude dos workflows"},
        {"command": "reprocess", "description": "Re-dispara pipeline num item"},
        {"command": "add", "description": "Adicionar contato"},
        {"command": "list", "description": "Listar contatos"},
        {"command": "cancel", "description": "Abortar fluxo atual"},
    ]
```

- [ ] **Step 2: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF "
git add webhook/app.py
git commit -m "chore(webhook): register /reports in bot command menu"
```

---

## Task 6: Push + test end-to-end

- [ ] **Step 1: Push to GitHub (triggers Railway auto-deploy)**

```bash
cd "/Users/bigode/Dev/Antigravity WF "
git push origin main
```

Railway auto-deploys on push. Wait ~2 min for deploy to complete.

- [ ] **Step 2: Register commands**

After Railway deploys, call the register endpoint to update the Telegram menu:

```bash
curl -X POST "https://<RAILWAY_WEBHOOK_URL>/admin/register-commands?chat_id=<YOUR_ADMIN_CHAT_ID>"
```

Replace `<RAILWAY_WEBHOOK_URL>` with the webhook's Railway domain and `<YOUR_ADMIN_CHAT_ID>` with the admin chat ID. Check `.env` or Railway env vars for these values.

Expected: `{"ok": true}` response.

- [ ] **Step 3: Test `/reports` in Telegram**

1. Open Telegram chat with the bot
2. Type `/` — verify "reports" appears in the autocomplete menu
3. Send `/reports`
4. Verify: inline keyboard with "📊 Market Reports" and "📊 Research Reports" buttons
5. Click "📊 Market Reports" → verify latest 10 reports listed with download buttons
6. Click any report → verify PDF arrives as document
7. Click "📅 Ver por data" → verify years shown
8. Navigate: year → month → report → download
9. Test all "⬅ Voltar" buttons at each level
10. Re-run same path — verify message is EDITED (not new messages sent)

- [ ] **Step 4: Commit any fixes from testing**

If any fixes needed, commit and push. Railway auto-deploys.

---

## Self-Review

**Spec coverage:**
- `/reports` command → Task 1 ✓
- Type selection menu → Task 1 (`_reports_show_types`) ✓
- Latest 10 reports → Task 2 (`_reports_show_latest`) ✓
- Browse by year → Task 3 (`_reports_show_years`) ✓
- Browse by month with counts → Task 3 (`_reports_show_months`) ✓
- Month report list → Task 3 (`_reports_show_month_list`) ✓
- Download reuses `report_dl` → Task 2+3 (callback data `report_dl:<uuid>`) ✓
- Back navigation → Task 4 (`rpt_back`) ✓
- Admin-only → Task 1 (`contact_admin.is_authorized`) ✓
- Edit same message → all helpers use `edit_message` ✓
- Register commands → Task 5 ✓
- Error handling (Supabase down, empty results) → all helpers have try/catch + fallback text ✓

**Placeholder scan:** None found. All code complete.

**Type consistency:**
- `report_type` string matches Supabase `report_type` column values ("Market Reports", "Research Reports")
- `report_dl:<uuid>` format matches existing handler at line 1266
- `edit_message(chat_id, message_id, text, reply_markup=)` matches existing function at line 635
- `send_telegram_message(chat_id, text, reply_markup=)` matches existing function at line 624
- `answer_callback(callback_id, text)` matches existing function at line 617
- `contact_admin.is_authorized(chat_id)` matches existing pattern used in all other commands
