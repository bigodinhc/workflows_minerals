#!/usr/bin/env python3
"""Trigger the platts-scrap-reports Apify actor and record run state.

Scheduled daily via .github/workflows/platts_reports.yml.
"""
import argparse
import json
import os
import sys
import traceback

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from execution.core import state_store
from execution.core.sentry_init import init_sentry
from execution.integrations.apify_client import ApifyClient

ACTOR_ID = os.getenv("APIFY_PLATTS_REPORTS_ACTOR_ID", "bigodeio05/platts-scrap-reports")
WORKFLOW_NAME = "platts_reports"


def main() -> int:
    init_sentry(__name__)
    parser = argparse.ArgumentParser(description="Trigger Platts Reports actor")
    parser.add_argument("--dry-run", action="store_true", help="Pass dryRun=true to the actor")
    parser.add_argument("--force-redownload", action="store_true")
    args = parser.parse_args()

    username = os.environ.get("PLATTS_USERNAME")
    password = os.environ.get("PLATTS_PASSWORD")
    if not username or not password:
        print("ERROR: PLATTS_USERNAME and PLATTS_PASSWORD required", file=sys.stderr)
        return 2

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

    print(f"Triggering actor {ACTOR_ID} (dryRun={args.dry_run})")
    client = ApifyClient()
    try:
        dataset_id = client.run_actor(ACTOR_ID, run_input, memory_mbytes=4096, timeout_secs=900)
        items = client.get_dataset_items(dataset_id)
    except Exception as e:
        print(f"Actor run failed: {e}", file=sys.stderr)
        traceback.print_exc()
        state_store.record_crash(WORKFLOW_NAME, f"{type(e).__name__}: {str(e)}"[:200])
        return 1

    if not items:
        print("Actor returned no dataset items")
        state_store.record_empty(WORKFLOW_NAME, "actor returned no items")
        return 0

    summary = items[0]
    print(json.dumps(summary, indent=2, default=str))

    summary_for_state = {
        "type": summary.get("type"),
        "downloaded_count": len(summary.get("downloaded", [])),
        "skipped_count": len(summary.get("skipped", [])),
        "errors_count": len(summary.get("errors", [])),
    }
    if summary.get("type") == "error":
        state_store.record_failure(WORKFLOW_NAME, summary_for_state, 0)
        return 1
    state_store.record_success(WORKFLOW_NAME, summary_for_state, 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
