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

from execution.integrations.lseg_client import LSEGClient
from execution.integrations.sheets_client import SheetsClient
from execution.integrations.uazapi_client import UazapiClient
from execution.core.logger import WorkflowLogger

# CONFIG
SHEET_ID = "1tU3Izdo21JichTXg15bc1paWUiN8XioJYZUPpbIUgL0" 
SHEET_NAME = "Página1"

def format_price_message(prices):
    """
    Formats the price list into the monospaced WhatsApp table.
    Expects 'prices' to be a list of dicts: {month, price, change, pct_change}
    """
    from datetime import timezone, timedelta
    
    # Horário de Brasília (UTC-3)
    BRT = timezone(timedelta(hours=-3))
    now = datetime.now(BRT).strftime("%d/%m/%y - %H:%M")
    
    lines = []
    lines.append(f"📈 MINERALS TRADING - [{now}]")
    lines.append("SGX IRON ORE 62% FE FUTURES")
    lines.append("────────────────────────────")
    lines.append("EXP    | PRICE  | CHG   | %")
    lines.append("────────────────────────────")
    
    for p in prices:
        # Determine dot color
        change_val = float(p.get('change', 0))
        if change_val > 0:
            emoji = "🟢"
            sign = "+"
        elif change_val < 0:
            emoji = "🔴"
            sign = ""
        else:
            emoji = ""
            sign = ""
            
        # Format columns fixed width
        month = str(p.get('month', '???')).ljust(6)
        price = f"{float(p.get('price', 0)):.2f}".rjust(6)
        
        # Format change with sign
        chg_fmt = f"{sign}{change_val:.2f}".rjust(6)
        
        # Format pct (ensure sign is handled)
        pct_val = float(p.get('pct_change', 0))
        # If pct is already signed/abs, handle logic. Assuming raw value.
        # LSEG might give 0.1 for 0.1%. Let's assume input is % number.
        pct_sign = "+" if pct_val > 0 else ""
        pct_fmt = f"{pct_sign}{pct_val:.2f}".rjust(6)
        
        line = f"{month} | {price} | {chg_fmt} | {pct_fmt} {emoji}"
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
        sheets = SheetsClient()
        contacts = sheets.get_contacts(SHEET_ID, SHEET_NAME)
        
        if not contacts:
            logger.warning("No contacts found to send to.")
            if lseg: lseg.close()
            return

        # 4. Send Messages
        uazapi = UazapiClient()
        success_count = 0
        fail_count = 0
        
        logger.info(f"Starting broadcast to {len(contacts)} contacts...")
        
        for contact in contacts:
            phone = contact.get('Evolution-api') or contact.get('Telefone') or contact.get('Phone')
            
            if not phone:
                continue
            
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
