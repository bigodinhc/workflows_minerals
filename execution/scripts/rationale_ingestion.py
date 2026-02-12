
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
from execution.agents.rationale_agent import RationaleAgent
from execution.core.logger import WorkflowLogger

# Actor from n8n: "PLATTS_NEWS_ONLY"
ACTOR_ID = os.getenv("APIFY_ACTOR_ID", "BgIGjvJDUhFgyE881")
DRAFTS_FILE = os.path.join(os.path.dirname(__file__), "../../data/news_drafts.json")

def save_draft(draft):
    """Saves a draft to the JSON storage"""
    os.makedirs(os.path.dirname(DRAFTS_FILE), exist_ok=True)
    
    if os.path.exists(DRAFTS_FILE):
        with open(DRAFTS_FILE, 'r') as f:
            try:
                drafts = json.load(f)
            except:
                drafts = []
    else:
        drafts = []
        
    # Append new draft
    drafts.append(draft)
    
    with open(DRAFTS_FILE, 'w') as f:
        json.dump(drafts, f, indent=2, ensure_ascii=False)

def main():
    logger = WorkflowLogger("RationaleIngestion")
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    
    try:
        # 1. Prepare Date
        today_br = datetime.now().strftime("%d/%m/%Y")
        logger.info(f"Starting ingestion for date: {today_br}")
        
        # Check if webhook already has a draft (avoid duplicate sends on retry crons)
        webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL", "")
        if webhook_url and not args.dry_run:
            import requests
            try:
                health = requests.get(f"{webhook_url}/health", timeout=5).json()
                if health.get("drafts_count", 0) > 0:
                    logger.info(f"Webhook already has {health['drafts_count']} draft(s). Skipping to avoid duplicate.")
                    return
            except Exception:
                pass  # If webhook unreachable, continue anyway
        
        # 2. Run Apify
        client = ApifyClient()
        logger.info(f"Targeting Apify Actor ID: {ACTOR_ID}")
        
        run_input = {
            "username": os.getenv("PLATTS_USERNAME", "antonio@mineralstrading.com.br"),
            "password": os.getenv("PLATTS_PASSWORD", "141204*MtM"), # Should use Env Var ideally
            "maxArticles": 5,
            "collectMarketCommentary": True,
            # "dateFilter": "specificDate", # Let actor default to Today (server time)
            # "targetDate": today_br,       # Avoid sending 2026 if real world is 2025
            "saveScreenshots": True
        }
        
        if args.dry_run:
            logger.info("[DRY RUN] Would run Apify with input: " + str(run_input))
            # Test Mock Data
            items = [{"title": "Test News", "fullText": "Iron ore prices rose ($130.50) amid China stimulus."}]
        else:
            logger.info("Running Apify Actor...")
            dataset_id = client.run_actor(ACTOR_ID, run_input, memory_mbytes=2048)
            items = client.get_dataset_items(dataset_id)
            
        if not items:
            logger.warning("No articles found today.")
            return

        # Dataset returns wrapper object with "articles" array inside
        if len(items) == 1 and "articles" in items[0]:
            logger.info("Detected wrapper object, extracting articles array...")
            articles = items[0].get("articles", [])
        else:
            articles = items
            
        if not articles:
            logger.warning("No articles in dataset.")
            return

        # 3. Aggregate Text for AI
        logger.info(f"Found {len(articles)} articles. Aggregating...")
        combined_text = "\n\n".join([
            f"=== ARTICLE {i+1} ===\nTitle: {item.get('title')}\nDate: {item.get('gridDateTime')}\n\n{item.get('fullText', '')}"
            for i, item in enumerate(articles)
        ])
        
        # Check for empty content
        if not combined_text.strip():
            logger.warning("Articles have no text content.")
            return

        # Check for minimal content to avoid hallucinations
        if len(combined_text) < 200:
            logger.warning(f"Combined text is too short ({len(combined_text)} chars). Skipping Agent to avoid hallucinations.")
            draft_text = "⚠️ **Sem Destaques Relevantes**\n\nO robô encontrou artigos, mas o conteúdo é insuficiente para gerar uma análise confiável hoje."
        else:
            # 4. Generate Draft via AI
            logger.info("Generating AI Draft...")
            agent = RationaleAgent()
            
            # Pass today's date for title formatting
            draft_text = agent.process(combined_text, today_br)
        
        # 5. Save Draft (Human-in-the-loop)
        draft_obj = {
            "id": f"draft_{int(datetime.now().timestamp())}",
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "source_date": today_br,
            "original_count": len(articles),
            "ai_text": draft_text,
            "source_summary": (articles[0].get('title') or "Sem Título") + "..." # Brief preview
        }
        
        if not args.dry_run:
            save_draft(draft_obj)
            logger.info("Draft saved successfully!")
            
            # 6. Send to Telegram for approval
            logger.info("Sending to Telegram for approval...")
            try:
                from execution.integrations.telegram_client import TelegramClient
                telegram = TelegramClient()
                
                # Also store draft on webhook server for callback processing
                webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL", "")
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
                # Don't fail workflow - draft is still saved for Dashboard
        else:
            logger.info("[DRY RUN] Draft generated but not saved:")
            print(draft_text)

    except Exception as e:
        logger.critical(f"Workflow failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()

