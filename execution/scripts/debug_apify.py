
import requests
import os
import json
import sys
from dotenv import load_dotenv

# Load env vars
load_dotenv(os.path.join(os.path.dirname(__file__), "../../.env"))

TOKEN = os.getenv("APIFY_API_TOKEN")
DATASET_ID = "U8cZtEYLn5VirmWxQ" # Dataset from run jh06eWY6E6LcKlVd9

if not TOKEN:
    print("Error: APIFY_API_TOKEN not found")
    sys.exit(1)

url = f"https://api.apify.com/v2/datasets/{DATASET_ID}/items?token={TOKEN}"

try:
    print(f"Fetching dataset {DATASET_ID}...")
    r = requests.get(url)
    r.raise_for_status()
    data = r.json()
    
    if not data:
        print("Dataset is empty []")
    else:
        print(f"Found {len(data)} items. Printing first item keys and content summary:")
        item = data[0]
        print(json.dumps(item, indent=2, ensure_ascii=False))
        
except Exception as e:
    print(f"Error: {e}")
