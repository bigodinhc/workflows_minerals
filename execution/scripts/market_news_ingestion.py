
#!/usr/bin/env python3
import sys
import os
import json
import argparse
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Load env vars from project root
load_dotenv(os.path.join(os.path.dirname(__file__), "../../.env"))

# Add project root to path
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from execution.integrations.apify_client import ApifyClient
from execution.agents.market_news_agent import MarketNewsAgent
from execution.core.logger import WorkflowLogger

# Actor: platts-scrap-full-news
ACTOR_ID = os.getenv("APIFY_MARKET_NEWS_ACTOR_ID", "bigodeio05/platts-scrap-full-news")
DRAFTS_FILE = os.path.join(os.path.dirname(__file__), "../../data/news_drafts.json")


def save_draft(draft):
    """Saves a draft to the JSON storage."""
    os.makedirs(os.path.dirname(DRAFTS_FILE), exist_ok=True)

    if os.path.exists(DRAFTS_FILE):
        with open(DRAFTS_FILE, 'r') as f:
            try:
                drafts = json.load(f)
            except (json.JSONDecodeError, ValueError):
                drafts = []
    else:
        drafts = []

    drafts.append(draft)

    with open(DRAFTS_FILE, 'w') as f:
        json.dump(drafts, f, indent=2, ensure_ascii=False)


def fetch_seen_articles(webhook_url, date_iso, logger=None):
    """Fetch previously seen article titles from the webhook server."""
    import requests
    try:
        resp = requests.get(
            f"{webhook_url}/seen-articles",
            params={"date": date_iso},
            timeout=10
        )
        if resp.status_code == 200:
            return set(resp.json().get("titles", []))
    except Exception as e:
        if logger:
            logger.warning(f"Failed to fetch seen articles: {e}")
    return set()


def store_seen_articles(webhook_url, date_iso, titles, logger=None):
    """Store new article titles on the webhook server."""
    import requests
    try:
        requests.post(
            f"{webhook_url}/seen-articles",
            json={"date": date_iso, "titles": list(titles)},
            timeout=10
        )
    except Exception as e:
        if logger:
            logger.warning(f"Failed to store seen articles: {e}")


def main():
    logger = WorkflowLogger("MarketNewsIngestion")
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--target-date", type=str, default="",
                        help="Data alvo DD/MM/YYYY. Vazio = hoje.")
    args = parser.parse_args()

    try:
        # 1. Prepare Date
        if args.target_date:
            today_br = args.target_date
            logger.info(f"Using target date: {today_br}")
            try:
                date_iso = datetime.strptime(today_br, "%d/%m/%Y").strftime("%Y-%m-%d")
            except ValueError:
                logger.error(f"Invalid date format: {today_br}. Expected DD/MM/YYYY")
                sys.exit(1)
        else:
            today_br = datetime.now().strftime("%d/%m/%Y")
            date_iso = datetime.now().strftime("%Y-%m-%d")
        logger.info(f"Starting market news ingestion for date: {today_br}")

        webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL", "")

        # 2. Fetch seen articles for dedup
        seen_titles = set()
        if webhook_url and not args.dry_run:
            seen_titles = fetch_seen_articles(webhook_url, date_iso, logger)
            logger.info(f"Found {len(seen_titles)} previously seen articles for {date_iso}")

        # 3. Run Apify Actor
        client = ApifyClient()
        logger.info(f"Targeting Apify Actor: {ACTOR_ID}")

        run_input = {
            "username": os.getenv("PLATTS_USERNAME", ""),
            "password": os.getenv("PLATTS_PASSWORD", ""),
            "maxArticles": 10,
            "collectMarketCommentary": True,
            "dateFilter": "today",
        }

        if args.target_date:
            run_input["targetDate"] = args.target_date
            run_input["dateFormat"] = "BR"
            del run_input["dateFilter"]

        if args.dry_run:
            logger.info("[DRY RUN] Would run Apify with input: " + str(run_input))
            items = [{
                "type": "success",
                "topNews": [{
                    "title": "Test Market News",
                    "fullText": "Iron ore spot prices fell $1.20 to $107.30/dmt CFR China amid weak steel margins. Vale shipped 80Mt in Q4. BHP reported record production at Western Australia operations.",
                    "date": today_br,
                    "source": "Top News - Ferrous Metals",
                    "author": "Test Author"
                }],
                "allArticles": [{
                    "title": "Test Market News",
                    "fullText": "Iron ore spot prices fell $1.20 to $107.30/dmt CFR China amid weak steel margins. Vale shipped 80Mt in Q4. BHP reported record production at Western Australia operations.",
                    "date": today_br,
                    "source": "Top News - Ferrous Metals",
                    "author": "Test Author"
                }],
                "summary": {"totalArticles": 1}
            }]
        else:
            logger.info("Running Apify Actor...")
            dataset_id = client.run_actor(ACTOR_ID, run_input, memory_mbytes=2048)
            items = client.get_dataset_items(dataset_id)

        if not items:
            logger.warning("No articles found today.")
            return

        # Actor returns wrapper: {type, topNews, allArticles, summary}
        # Use allArticles (most complete list) with topNews as fallback
        if len(items) == 1 and isinstance(items[0], dict):
            wrapper = items[0]
            articles = wrapper.get("allArticles") or wrapper.get("topNews") or wrapper.get("articles") or []
            total = wrapper.get("summary", {}).get("totalArticles", len(articles))
            logger.info(f"Detected wrapper object (type={wrapper.get('type')}), extracted {len(articles)} articles (summary says {total})")
        else:
            articles = items

        if not articles:
            logger.warning("No articles in dataset.")
            return

        logger.info(f"Found {len(articles)} articles from actor.")

        # 4. Deduplicate
        new_articles = [
            a for a in articles
            if a.get("title", "") not in seen_titles
        ]
        logger.info(f"After dedup: {len(new_articles)} new articles (filtered {len(articles) - len(new_articles)} seen)")

        if not new_articles:
            logger.info("No new articles after dedup. Exiting cleanly.")
            return

        # 5. Aggregate Text for AI
        combined_text = "\n\n".join([
            f"=== ARTICLE {i+1} ===\nTitle: {item.get('title')}\nDate: {item.get('date') or item.get('publishDate') or item.get('gridDateTime', '')}\nSource: {item.get('source', '')}\n\n{item.get('fullText', '')}"
            for i, item in enumerate(new_articles)
        ])

        if not combined_text.strip():
            logger.warning("Articles have no text content.")
            return

        if len(combined_text) < 200:
            logger.warning(f"Combined text is too short ({len(combined_text)} chars). Skipping Agent to avoid hallucinations.")
            draft_text = "⚠️ **Sem Destaques Relevantes**\n\nO robô encontrou artigos, mas o conteúdo é insuficiente para gerar uma análise confiável."
        else:
            # 6. Generate Draft via AI
            logger.info("Generating AI Draft via MarketNewsAgent...")
            agent = MarketNewsAgent()
            draft_text = agent.process(combined_text, today_br)

        # 7. Save Draft
        draft_obj = {
            "id": f"market_{int(datetime.now().timestamp())}",
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "source_date": today_br,
            "original_count": len(new_articles),
            "ai_text": draft_text,
            "source_summary": (new_articles[0].get('title') or "Sem Título") + "..."
        }

        if not args.dry_run:
            save_draft(draft_obj)
            logger.info("Draft saved successfully!")

            # 8. Store new titles on webhook for future dedup
            new_titles = [a.get("title", "") for a in new_articles if a.get("title")]
            if webhook_url and new_titles:
                store_seen_articles(webhook_url, date_iso, new_titles, logger)
                logger.info(f"Stored {len(new_titles)} new article titles for dedup")

            # 9. Send to Telegram for approval
            logger.info("Sending to Telegram for approval...")
            try:
                from execution.integrations.telegram_client import TelegramClient
                telegram = TelegramClient()

                # Store draft on webhook server for callback processing
                if webhook_url:
                    import requests
                    try:
                        requests.post(
                            f"{webhook_url}/store-draft",
                            json={
                                "draft_id": draft_obj["id"],
                                "message": draft_text,
                                "uazapi_token": os.getenv("UAZAPI_TOKEN", ""),
                                "uazapi_url": os.getenv("UAZAPI_URL", "https://mineralstrading.uazapi.com")
                            },
                            timeout=10
                        )
                    except Exception as store_err:
                        logger.warning(f"Could not store draft on webhook: {store_err}")

                # Send Telegram message with buttons
                telegram.send_approval_request(
                    draft_id=draft_obj["id"],
                    preview_text=draft_text
                )
                logger.info("Telegram notification sent!")

            except Exception as tg_err:
                logger.error(f"Failed to send Telegram notification: {tg_err}")
        else:
            logger.info("[DRY RUN] Draft generated but not saved:")
            print(draft_text)

    except Exception as e:
        logger.critical(f"Workflow failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
