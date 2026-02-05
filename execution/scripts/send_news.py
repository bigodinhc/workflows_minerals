
#!/usr/bin/env python3
import sys
import os
import argparse
import time

# Add project root to path
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from execution.integrations.sheets_client import SheetsClient
from execution.integrations.uazapi_client import UazapiClient
from execution.core.logger import WorkflowLogger

# Config (Same as other workflows)
SHEET_ID = "1tU3Izdo21JichTXg15bc1paWUiN8XioJYZUPpbIUgL0" 
SHEET_NAME = "Página1"

def main():
    logger = WorkflowLogger("SendNews")
    parser = argparse.ArgumentParser()
    parser.add_argument("--message", help="Message text to send")
    parser.add_argument("--file", help="Path to text file containing message")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    
    if args.file:
        with open(args.file, 'r', encoding='utf-8') as f:
            msg = f.read()
    elif args.message:
        msg = args.message
    else:
        logger.critical("Either --message or --file is required")
        sys.exit(1)
    
    # 1. Fetch Contacts
    logger.info("Fetching contacts...")
    try:
        sheets = SheetsClient()
        contacts = sheets.get_contacts(SHEET_ID, SHEET_NAME)
    except Exception as e:
        logger.critical(f"Failed to fetch contacts: {e}")
        sys.exit(1)
        
    if not contacts:
        logger.warning("No contacts found.")
        sys.exit(0)
        
    # 2. Send
    uazapi = UazapiClient()
    logger.info(f"Broadcasting to {len(contacts)} contacts...")
    
    success = 0
    fail = 0
    
    for contact in contacts:
        raw_phone = (
            contact.get('Evolution-api') or 
            contact.get('Telefone') or 
            contact.get('Phone') 
        )
        
        if not raw_phone: continue
        
        phone = str(raw_phone).replace("whatsapp:", "").strip()
        
        if args.dry_run:
            logger.info(f"[DRY RUN] Would send to {phone}")
            success += 1
        else:
            try:
                uazapi.send_message(phone, msg)
                logger.info(f"Sent to {phone}")
                success += 1
            except Exception as e:
                logger.error(f"Failed to send to {phone}: {e}")
                fail += 1
            
            time.sleep(2) # Rate limit
    
    logger.info(f"Finished. Success: {success}, Fail: {fail}")

if __name__ == "__main__":
    main()
