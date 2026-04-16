#!/usr/bin/env python3
"""Unified Platts ingestion: scrape → dedup → route to rationale AI or Telegram curation.

Replaces rationale_ingestion.py and market_news_ingestion.py.
Scheduled 3x/day (9h, 12h, 15h BRT) via Railway cron.
"""
import argparse
import os
import sys
import traceback
from datetime import datetime

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from execution.core import state_store
from execution.core.logger import WorkflowLogger
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
        if args.target_date:
            run_input["targetDate"] = args.target_date
            run_input["dateFormat"] = "BR"
            run_input["dateFilter"] = "all"

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
            logger.info(f"Running Apify Actor: {ACTOR_ID}")
            client = ApifyClient()
            dataset_id = client.run_actor(ACTOR_ID, run_input, memory_mbytes=8192)
            items = client.get_dataset_items(dataset_id)

        articles = _flatten_dataset(items)
        logger.info(f"Flattened to {len(articles)} articles.")

        if not articles:
            logger.warning("No articles after flatten.")
            state_store.record_empty(WORKFLOW_NAME, "scrape vazio")
            return

        counters, staged = router.route_items(
            items=articles,
            today_date=date_iso,
            today_br=today_br,
            logger=logger,
        )
        logger.info(f"Route summary: {counters}")

        # v1.1: send single ingestion digest if any new items were staged
        if counters.get("staged", 0) > 0:
            try:
                from webhook.digest import format_ingestion_digest
                from execution.integrations.telegram_client import TelegramClient
                digest_out = format_ingestion_digest(counters, staged)
                if digest_out is not None:
                    text, markup = digest_out
                    TelegramClient().send_message(
                        text=text, chat_id=chat_id, reply_markup=markup,
                    )
                    logger.info(f"Digest sent to chat {chat_id}")
            except Exception as exc:
                logger.warning(f"Digest send failed: {exc}")

        state_store.record_success(WORKFLOW_NAME, counters, 0)

    except Exception as e:
        logger.critical(
            f"Workflow failed ({type(e).__name__}): {e}\n{traceback.format_exc()}"
        )
        state_store.record_crash(WORKFLOW_NAME, f"{type(e).__name__}: {str(e)}"[:200])
        raise


if __name__ == "__main__":
    main()
