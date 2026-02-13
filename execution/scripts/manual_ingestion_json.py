import json
import os
import sys
from datetime import datetime
from dotenv import load_dotenv

# Add parent dir to sys.path to allow imports
sys.path.append(os.path.join(os.path.dirname(__file__), '../../'))

from execution.agents.rationale_agent import RationaleAgent
from execution.core.logger import WorkflowLogger

# Load env vars
load_dotenv(os.path.join(os.path.dirname(__file__), "../../.env"))

def save_draft(draft_data):
    drafts_file = os.path.join(os.path.dirname(__file__), '../../data/news_drafts.json')
    
    if os.path.exists(drafts_file):
        with open(drafts_file, 'r') as f:
            try:
                drafts = json.load(f)
            except:
                drafts = []
    else:
        drafts = []
        
    drafts.append(draft_data)
    
    with open(drafts_file, 'w') as f:
        json.dump(drafts, f, indent=2, ensure_ascii=False)

def main():
    logger = WorkflowLogger("ManualJsonIngestion")
    json_path = "/Users/bigode/Dev/Antigravity WF /dataset_platts-news-only_2026-02-05_20-49-22-793.json"
    
    try:
        if not os.path.exists(json_path):
            logger.critical("File not found.")
            return

        with open(json_path, 'r') as f:
            data = json.load(f)
            
        # Handle Array wrapper if present (the file seems to be wrapped in [ ... ])
        if isinstance(data, list):
            data = data[0]

        items = data.get("articles", [])
        
        if not items:
            logger.warning("No articles in JSON.")
            return

        # 3. Aggregate Text for AI
        logger.info(f"Found {len(items)} articles. Aggregating...")
        combined_text = "\n\n".join([
            f"=== ARTICLE {i+1} ===\nTitle: {item.get('title')}\nDate: {item.get('gridDateTime')}\n\n{item.get('fullText', '')}"
            for i, item in enumerate(items)
        ])
        
        # 4. Generate Draft via AI
        logger.info("Generating AI Draft...")
        agent = RationaleAgent()
        today_br = datetime.now().strftime("%d/%m/%Y")
        
        draft_text = agent.process(combined_text, today_br)
        
        # 5. Save Draft
        draft_obj = {
            "id": f"draft_{int(datetime.now().timestamp())}_manual",
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "source_date": today_br,
            "original_count": len(items),
            "ai_text": draft_text,
            "source_summary": (items[0].get('title') or "Sem Título") + "..." 
        }
        
        save_draft(draft_obj)
        logger.info("Draft saved successfully from LOCAL JSON.")

    except Exception as e:
        logger.critical(f"Workflow failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
