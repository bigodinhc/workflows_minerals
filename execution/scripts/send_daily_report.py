#!/usr/bin/env python3
"""
Orchestrates the daily price report collection and dissemination.
Unified Workflow: LSEG Fetch -> Format -> Send Uazapi
"""

import os
import sys
import time
import argparse
from datetime import datetime

# Adjust path to allow imports from root
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from execution.core.logger import WorkflowLogger

# CONFIG
SHEET_ID = "1tU3Izdo21JichTXg15bc1paWUiN8XioJYZUPpbIUgL0" 
SHEET_NAME = "Página1"

def format_price_message(prices):
    """
    Formats the price list with backtick-highlighted prices.
    Expects 'prices' to be a list of dicts: {month, price, change, pct_change}
    """
    from datetime import timezone, timedelta
    
    # Horário de Brasília (UTC-3)
    BRT = timezone(timedelta(hours=-3))
    now = datetime.now(BRT).strftime("%d/%m/%y - %H:%M")
    
    lines = []
    lines.append(f"📈 *MINERALS TRADING* - [{now}]")
    lines.append("*SGX IRON ORE 62% FE FUTURES*")
    lines.append("")
    
    for p in prices:
        # Determine dot color and sign
        change_val = float(p.get('change', 0))
        pct_val = float(p.get('pct_change', 0))
        
        if change_val > 0:
            emoji = "🟢"
            sign = "+"
            pct_sign = "+"
        elif change_val < 0:
            emoji = "🔴"
            sign = ""
            pct_sign = ""
        else:
            emoji = "⚪"
            sign = ""
            pct_sign = ""
            
        month = str(p.get('month', '???')).upper()
        price_float = float(p.get('price', 0))
        
        # Format change and pct
        chg_str = f"{sign}{change_val:.2f}" if change_val >= 0 else f"{change_val:.2f}"
        pct_str = f"{pct_sign}{pct_val:.2f}" if pct_val >= 0 else f"{pct_val:.2f}"
        
        # Format: *FEB/26* | `101.90` | -0.60 | -0.59 🔴
        line = f"*{month}* | `{price_float:.2f}` | {chg_str} | {pct_str} {emoji}"
        lines.append(line)
        
    return "\n".join(lines)


def main():
    logger = WorkflowLogger("DailyReport")
    
    # Check for DRY RUN mode
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Do not actually send messages")
    args = parser.parse_args()
    
    lseg = None
    
    try:
        # Import here to avoid dependency issues when testing format function
        from execution.integrations.lseg_client import LSEGClient
        
        # 1. Fetch Prices (LSEG)
        logger.info("Connecting to LSEG...")
        lseg = LSEGClient()
        lseg.connect()
        
        logger.info("Fetching latest futures data...")
        prices = lseg.get_futures_data()
        
        if not prices:
            logger.warning("No prices found/returned from LSEG!")
            if lseg: lseg.close()
            return
            
        logger.info(f"Got {len(prices)} contracts.")
            
        # 2. Format Message
        message = format_price_message(prices)
        logger.info("Message formatted successfully")
        print("\n--- PREVIEW ---\n" + message + "\n---------------\n")
        
        # 3. Fetch Contacts
        logger.info("Fetching contacts from Sheets...")
        from execution.integrations.sheets_client import SheetsClient
        sheets = SheetsClient()
        contacts = sheets.get_contacts(SHEET_ID, SHEET_NAME)
        
        if not contacts:
            logger.warning("No contacts found to send to.")
            if lseg: lseg.close()
            return

        # 4. Send Messages
        from execution.integrations.uazapi_client import UazapiClient
        uazapi = UazapiClient()
        success_count = 0
        fail_count = 0
        
        logger.info(f"Starting broadcast to {len(contacts)} contacts...")
        
        for contact in contacts:
            # Try to get phone from multiple convenient columns (Sheet Screenshot shows 'From')
            raw_phone = (
                contact.get('Evolution-api') or 
                contact.get('Telefone') or 
                contact.get('Phone') or 
                contact.get('From')
            )
            
            if not raw_phone:
                continue
                
            # Clean phone number (remove 'whatsapp:' prefix if present)
            phone = str(raw_phone).replace("whatsapp:", "").strip()
            
            if args.dry_run:
                logger.info(f"[DRY RUN] Would send to {phone}")
                success_count += 1
            else:
                try:
                    uazapi.send_message(phone, message)
                    logger.info(f"Sent to {phone}")
                    success_count += 1
                except Exception as e:
                    logger.error(f"Failed to send to {phone}", {"error": str(e)})
                    fail_count += 1
            
            time.sleep(3) # Rate limit
            
        logger.info("Broadcast finished", {"success": success_count, "fail": fail_count})

    except Exception as e:
        logger.critical("Workflow disrupted", {"error": str(e)})
        # Don't fail the action if it's just a connection glitch? 
        # Better to fail so we get notified.
        sys.exit(1)
    finally:
        if lseg:
            lseg.close()

if __name__ == "__main__":
    main()
