#!/usr/bin/env python3
"""Unified Platts ingestion: scrape → dedup → route to rationale AI or Telegram curation.

Replaces rationale_ingestion.py and market_news_ingestion.py.
Scheduled 3x/day (9h, 12h, 15h BRT) via Railway cron.
"""
import argparse
import asyncio
import os
import sys
import traceback
import uuid
from datetime import datetime

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from execution.core import state_store
import time as _time

from execution.core.event_bus import with_event_bus, get_current_bus
from execution.core.logger import WorkflowLogger
from execution.core.sentry_init import init_sentry
from execution.curation import router
from execution.integrations.apify_client import ApifyClient

ACTOR_ID = os.getenv("APIFY_PLATTS_ACTOR_ID", "bigodeio05/platts-scrap-full-news")
WORKFLOW_NAME = "platts_ingestion"


def _flatten_dataset(items: list) -> list:
    """Flatten merged-actor dataset shape into a flat list of article dicts.

    The actor returns a single wrapper with keys flash/topNews/latest/newsInsights/rmw.
    RMW is nested one level deeper as [{tabName, articles: [...]}].
    Defensive against malformed payloads — non-dict entries are skipped.
    """
    flat = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if any(k in item for k in ("topNews", "latest", "newsInsights", "rmw", "flash")):
            for key in ("flash", "topNews", "latest", "newsInsights"):
                val = item.get(key)
                if isinstance(val, list):
                    flat.extend(a for a in val if isinstance(a, dict))
            rmw_groups = item.get("rmw")
            if isinstance(rmw_groups, list):
                for group in rmw_groups:
                    if not isinstance(group, dict):
                        continue
                    tab = group.get("tabName", "")
                    articles = group.get("articles")
                    if not isinstance(articles, list):
                        continue
                    for a in articles:
                        if not isinstance(a, dict):
                            continue
                        a = dict(a)
                        a.setdefault("tabName", tab)
                        flat.append(a)
        else:
            flat.append(item)
    return flat


async def _run_with_progress(args, logger, chat_id: int, today_br: str, date_iso: str, run_input: dict) -> None:
    """Async ingestion body instrumented with ProgressReporter step() calls."""
    from aiogram import Bot
    from execution.core.progress_reporter import ProgressReporter

    bus = get_current_bus()
    bus.emit("step", label="Iniciando platts_ingestion")

    bot = Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
    sb = None
    try:
        from supabase import create_client
        sb = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_ROLE_KEY"],
        )
    except Exception as exc:
        logger.warning("supabase_init_failed in platts_ingestion: %s", exc)

    # Send initial placeholder card
    initial = await bot.send_message(chat_id, "📡 Platts Ingestion\n⏳ starting...")

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
        # ── PHASE 1: trigger Apify actor (or dry-run mock) ────────────────────
        await reporter.step("Actor started", "platts-scrap-full-news triggered")

        if args.dry_run:
            logger.info("[DRY RUN] Would run Apify with input: " + str(run_input))
            items = [{
                "type": "success",
                "topNews": [{
                    "title": "DryRun Top",
                    "fullText": "Test body with prices $104.80/dmt CFR.",
                    "publishDate": today_br,
                    "source": "Top News - Ferrous Metals",
                    "author": "Test Author",
                    "tabName": "",
                }],
                "rmw": [{
                    "tabName": "CFR North China Iron Ore 65% Fe Rationale",
                    "articles": [{
                        "title": "DryRun Rationale",
                        "fullText": "Platts assessed the 65% Fe index at $123.35/dmt CFR North China.",
                        "gridDateTime": today_br,
                        "source": "rmw.CFR North China Iron Ore 65% Fe Rationale",
                        "tabName": "CFR North China Iron Ore 65% Fe Rationale",
                    }],
                }],
                "summary": {"totalArticles": 2},
            }]
        else:
            bus.emit("step", label="Disparando Apify actor")
            logger.info(f"Running Apify Actor: {ACTOR_ID}")
            client = ApifyClient()
            t0 = _time.time()
            dataset_id, items = await asyncio.to_thread(
                _run_apify_sync, client, run_input,
            )
            bus.emit("api_call", label="apify.run", detail={"duration_ms": round((_time.time() - t0) * 1000), "rows": len(items) if items else 0})

        # ── PHASE 2: flatten dataset ───────────────────────────────────────────
        articles = _flatten_dataset(items)
        logger.info(f"Flattened to {len(articles)} articles.")
        await reporter.step("Dataset fetched", f"{len(articles)} articles after flatten")

        if not articles:
            logger.warning("No articles after flatten.")
            state_store.record_empty(WORKFLOW_NAME, "scrape vazio")
            await reporter.step("Empty result", "no articles — nothing to stage", level="warning")
            await reporter.finish()
            return

        # ── PHASE 3: route (dedup + stage) ────────────────────────────────────
        bus.emit("step", label="Processando dedup + Supabase")
        counters, staged = await asyncio.to_thread(
            router.route_items,
            items=articles,
            today_date=date_iso,
            today_br=today_br,
            logger=logger,
        )
        logger.info(f"Route summary: {counters}")

        new_count = counters.get("staged", 0)
        dup_count = counters.get("dedup_skipped", 0) + counters.get("dup", 0)
        await reporter.step("Dedup applied", f"{new_count} new, {dup_count} duplicates")

        staged_count = counters.get("staged", 0)
        await reporter.step("Staged in Redis", f"{staged_count} items")

        # ── PHASE 4: send ingestion digest ────────────────────────────────────
        preview_base_url = os.getenv("TELEGRAM_WEBHOOK_URL", "")
        if staged_count > 0:
            try:
                from webhook.digest import format_ingestion_digest
                from execution.integrations.telegram_client import TelegramClient
                digest_out = format_ingestion_digest(counters, staged)
                if digest_out is not None:
                    text, markup = digest_out
                    await asyncio.to_thread(
                        TelegramClient().send_message,
                        text,
                        chat_id,
                        markup,
                    )
                    logger.info(f"Digest sent to chat {chat_id}")
            except Exception as exc:
                logger.warning(f"Digest send failed: {exc}")

        state_store.record_success(WORKFLOW_NAME, counters, 0)

        await reporter.finish(message=f"✅ Done — {staged_count} staged")

    except Exception as exc:
        await reporter.step(
            f"Failed: {type(exc).__name__}",
            str(exc)[:200],
            level="error",
        )
        raise
    finally:
        await bot.session.close()


def _run_apify_sync(client: ApifyClient, run_input: dict):
    """Blocking helper: run actor and fetch items. Runs inside asyncio.to_thread."""
    dataset_id = client.run_actor(ACTOR_ID, run_input, memory_mbytes=8192)
    items = client.get_dataset_items(dataset_id)
    return dataset_id, items


@with_event_bus("platts_ingestion")
def main():
    logger = WorkflowLogger("PlattsIngestion")
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Skip Apify, use mock data")
    parser.add_argument("--target-date", type=str, default="",
                        help="Data alvo DD/MM/YYYY. Vazio = hoje.")
    args = parser.parse_args()

    try:
        if args.target_date:
            today_br = args.target_date
            try:
                date_iso = datetime.strptime(today_br, "%d/%m/%Y").strftime("%Y-%m-%d")
            except ValueError:
                logger.error(f"Invalid date: {today_br}. Expected DD/MM/YYYY")
                sys.exit(1)
        else:
            today_br = datetime.now().strftime("%d/%m/%Y")
            date_iso = datetime.now().strftime("%Y-%m-%d")
        logger.info(f"Starting ingestion for date: {today_br} (iso: {date_iso})")

        chat_id_raw = os.getenv("TELEGRAM_CHAT_ID", "0")
        try:
            chat_id = int(chat_id_raw)
        except ValueError:
            logger.error(f"TELEGRAM_CHAT_ID not a valid integer: {chat_id_raw!r}")
            sys.exit(1)
        preview_base_url = os.getenv("TELEGRAM_WEBHOOK_URL", "")
        if not chat_id or not preview_base_url:
            logger.error("TELEGRAM_CHAT_ID or TELEGRAM_WEBHOOK_URL not set.")
            sys.exit(1)

        bus = get_current_bus()
        run_input = {
            "username": os.getenv("PLATTS_USERNAME", ""),
            "password": os.getenv("PLATTS_PASSWORD", ""),
            "sources": ["allInsights", "ironOreTopic", "rmw"],
            "includeFlash": True,
            "includeLatest": True,
            "maxArticles": 50,
            "maxArticlesPerRmwTab": 5,
            "latestMaxItems": 15,
            "dateFilter": "today",
            "concurrency": 2,
            "dedupArticles": True,
        }
        if bus is not None:
            run_input["trace_id"] = bus.trace_id
            run_input["parent_run_id"] = bus.run_id
        if args.target_date:
            run_input["targetDate"] = args.target_date
            run_input["dateFormat"] = "BR"
            run_input["dateFilter"] = "all"

        asyncio.run(_run_with_progress(args, logger, chat_id, today_br, date_iso, run_input))

    except Exception as e:
        logger.critical(
            f"Workflow failed ({type(e).__name__}): {e}\n{traceback.format_exc()}"
        )
        state_store.record_crash(WORKFLOW_NAME, f"{type(e).__name__}: {str(e)}"[:200])
        raise


if __name__ == "__main__":
    main()
