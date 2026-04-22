#!/usr/bin/env python3
"""Trigger the platts-scrap-reports Apify actor and record run state.

Scheduled daily via .github/workflows/platts_reports.yml.
"""
import argparse
import asyncio
import json
import os
import sys
import traceback
import uuid

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from execution.core import state_store
import time as _time

from execution.core.event_bus import with_event_bus, get_current_bus
from execution.core.sentry_init import init_sentry
from execution.integrations.apify_client import ApifyClient

ACTOR_ID = os.getenv("APIFY_PLATTS_REPORTS_ACTOR_ID", "bigodeio05/platts-scrap-reports")
WORKFLOW_NAME = "platts_reports"


def _run_apify_sync(client: ApifyClient, run_input: dict):
    """Blocking helper: run actor and fetch items. Runs inside asyncio.to_thread."""
    dataset_id = client.run_actor(ACTOR_ID, run_input, memory_mbytes=4096, timeout_secs=900)
    items = client.get_dataset_items(dataset_id)
    return dataset_id, items


async def _run_with_progress(args, chat_id: int, run_input: dict) -> int:
    """Async reports body instrumented with ProgressReporter step() calls."""
    from aiogram import Bot
    from execution.core.progress_reporter import ProgressReporter

    bus = get_current_bus()
    bus.emit("step", label="Iniciando platts_reports")

    bot = Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
    sb = None
    try:
        from supabase import create_client
        sb = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_ROLE_KEY"],
        )
    except Exception as exc:
        print(f"WARNING: supabase_init_failed in platts_reports: {exc}", file=sys.stderr)

    # Send initial placeholder card
    initial = await bot.send_message(chat_id, "📊 Platts Reports\n⏳ starting...")

    reporter = ProgressReporter(
        bot=bot,
        chat_id=chat_id,
        workflow=WORKFLOW_NAME,
        run_id=str(uuid.uuid4()),
        supabase_client=sb,
    )
    reporter._message_id = initial.message_id
    reporter._pending_card_state = []
    reporter._last_edit_at = None

    try:
        # ── PHASE 1: trigger Apify actor ──────────────────────────────────────
        await reporter.step("Actor started", f"{ACTOR_ID} triggered")

        bus.emit("step", label="Actor Apify triggered")
        t0 = _time.time()
        dataset_id, items = await asyncio.to_thread(_run_apify_sync, ApifyClient(), run_input)
        bus.emit("api_call", label="apify.reports.run", detail={"duration_ms": round((_time.time() - t0) * 1000), "rows": len(items) if items else 0})

        # ── PHASE 2: dataset received ─────────────────────────────────────────
        await reporter.step("Dataset received", f"{len(items)} item(s) from actor")

        if not items:
            state_store.record_empty(WORKFLOW_NAME, "actor returned no items")
            await reporter.step("Empty result", "actor returned no items", level="warning")
            await reporter.finish()
            return 0

        summary = items[0]
        print(json.dumps(summary, indent=2, default=str))

        downloaded = summary.get("downloaded", [])
        skipped = summary.get("skipped", [])
        errors = summary.get("errors", [])
        downloaded_count = len(downloaded)
        skipped_count = len(skipped)
        errors_count = len(errors)

        # ── PHASE 3: PDFs downloaded ──────────────────────────────────────────
        bus.emit("step", label=f"PDFs baixados: {downloaded_count}, erros: {errors_count}")
        await reporter.step(
            "PDFs downloaded",
            f"{downloaded_count} downloaded, {skipped_count} skipped, {errors_count} errors",
        )

        # ── PHASE 4: Supabase / storage result ───────────────────────────────
        # The actor handles upload internally; we report the outcome.
        uploaded_count = len([r for r in downloaded if r.get("supabaseUrl") or r.get("storagePath")])
        await reporter.step(
            "Uploaded to Supabase",
            f"{uploaded_count} objects stored (of {downloaded_count} downloaded)",
        )

        summary_for_state = {
            "type": summary.get("type"),
            "downloaded_count": downloaded_count,
            "skipped_count": skipped_count,
            "errors_count": errors_count,
        }

        if summary.get("type") == "error":
            state_store.record_failure(WORKFLOW_NAME, summary_for_state, 0)
            await reporter.step(
                "Actor reported error",
                summary.get("message", "")[:200],
                level="error",
            )
            return 1

        state_store.record_success(WORKFLOW_NAME, summary_for_state, 0)

        await reporter.finish(
            message=f"✅ Done — {downloaded_count} reports downloaded, {uploaded_count} uploaded"
        )
        return 0

    except Exception as exc:
        await reporter.step(
            f"Failed: {type(exc).__name__}",
            str(exc)[:200],
            level="error",
        )
        raise
    finally:
        await bot.session.close()


@with_event_bus("platts_reports")
def main() -> int:
    parser = argparse.ArgumentParser(description="Trigger Platts Reports actor")
    parser.add_argument("--dry-run", action="store_true", help="Pass dryRun=true to the actor")
    parser.add_argument("--force-redownload", action="store_true")
    args = parser.parse_args()

    username = os.environ.get("PLATTS_USERNAME")
    password = os.environ.get("PLATTS_PASSWORD")
    if not username or not password:
        print("ERROR: PLATTS_USERNAME and PLATTS_PASSWORD required", file=sys.stderr)
        return 2

    chat_id_raw = os.getenv("TELEGRAM_CHAT_ID", "0")
    try:
        chat_id = int(chat_id_raw)
    except ValueError:
        print(f"ERROR: TELEGRAM_CHAT_ID not a valid integer: {chat_id_raw!r}", file=sys.stderr)
        return 2
    if not chat_id:
        print("ERROR: TELEGRAM_CHAT_ID not set.", file=sys.stderr)
        return 2

    bus = get_current_bus()
    run_input = {
        "username": username,
        "password": password,
        "reportTypes": ["Market Reports", "Research Reports"],
        "maxReportsPerType": 50,
        "dryRun": args.dry_run,
        "forceRedownload": args.force_redownload,
        "gdriveFolderId": os.environ.get(
            "GDRIVE_PLATTS_REPORTS_FOLDER_ID", "1KxixMP9rKF0vGzINGvmmyFvouaOvL02y"
        ),
    }
    if bus is not None:
        run_input["trace_id"] = bus.trace_id
        run_input["parent_run_id"] = bus.run_id

    try:
        return asyncio.run(_run_with_progress(args, chat_id, run_input))
    except Exception as e:
        print(
            f"Workflow failed ({type(e).__name__}): {e}\n{traceback.format_exc()}",
            file=sys.stderr,
        )
        state_store.record_crash(WORKFLOW_NAME, f"{type(e).__name__}: {str(e)}"[:200])
        return 1


if __name__ == "__main__":
    sys.exit(main())
