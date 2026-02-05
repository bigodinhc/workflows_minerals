
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
ACTOR_ID = "BgIGjvJDUhFgyE881"
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
        
        # 2. Run Apify
        client = ApifyClient()
        
        run_input = {
            "username": os.getenv("PLATTS_USERNAME", "antonio@mineralstrading.com.br"),
            "password": os.getenv("PLATTS_PASSWORD", "141204*MtM"), # Should use Env Var ideally
            "maxArticles": 5,
            "collectMarketCommentary": True,
            "dateFilter": "specificDate",
            "targetDate": today_br,
            "saveScreenshots": True
        }
        
        if args.dry_run:
            logger.info("[DRY RUN] Would run Apify with input: " + str(run_input))
            # Test Mock Data
            items = [{"title": "Test News", "fullText": "Iron ore prices rose ($130.50) amid China stimulus."}]
        else:
            logger.info("Running Apify Actor...")
            dataset_id = client.run_actor(ACTOR_ID, run_input)
            items = client.get_dataset_items(dataset_id)
            
        if not items:
            logger.warning("No articles found today.")
            return

        # 3. Aggregate Text for AI
        logger.info(f"Found {len(items)} articles. Aggregating...")
        combined_text = "\n\n".join([
            f"=== ARTICLE {i+1} ===\nTitle: {item.get('title')}\nDate: {item.get('gridDateTime')}\n\n{item.get('fullText', '')}"
            for i, item in enumerate(items)
        ])
        
        if not combined_text.strip():
            logger.warning("Articles have no text content.")
            return

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
            "original_count": len(items),
            "ai_text": draft_text,
            "source_summary": (items[0].get('title') or "Sem Título") + "..." # Brief preview
        }
        
        if not args.dry_run:
            save_draft(draft_obj)
            logger.info("Draft saved successfully! Waiting for approval.")
        else:
            logger.info("[DRY RUN] Draft generated but not saved:")
            print(draft_text)

    except Exception as e:
        logger.critical(f"Workflow failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
