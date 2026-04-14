#!/usr/bin/env python3
"""
Orchestrates the daily price report collection and dissemination.
Unified Workflow: LSEG Fetch -> Format -> Send Uazapi
"""

import os
import sys
import argparse
from datetime import datetime

# Adjust path to allow imports from root
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from execution.core.logger import WorkflowLogger
from execution.core.delivery_reporter import DeliveryReporter, Contact, build_contact_from_row

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
    now = datetime.now(BRT).strftime("%d/%m/%Y")
    
    # Month translation EN -> PT
    MONTHS_PT = {
        'JAN': 'Jan', 'FEB': 'Fev', 'MAR': 'Mar', 'APR': 'Abr',
        'MAY': 'Mai', 'JUN': 'Jun', 'JUL': 'Jul', 'AUG': 'Ago',
        'SEP': 'Set', 'OCT': 'Out', 'NOV': 'Nov', 'DEC': 'Dez'
    }
    
    def translate_month(month_code):
        """Converts 'FEB/26' or 'FEB26' to 'Fev/26'"""
        month_code = month_code.upper().replace("/", "")
        # Extract month part (first 3 chars) and year part (rest)
        month_part = month_code[:3]
        year_part = month_code[3:]
        pt_month = MONTHS_PT.get(month_part, month_part)
        return f"{pt_month}/{year_part}"
    
    lines = []
    lines.append("📊 *MINERALS TRADING DAILY REPORT* 📊")
    lines.append(f"📈  SGX IRON ORE 62% FE FUTURES - {now}")
    lines.append("")
    lines.append("⛏️ *CONTRATOS FUTUROS*")
    
    for p in prices:
        change_val = float(p.get('change', 0))
        pct_val = float(p.get('pct_change', 0))
        
        month_raw = str(p.get('month', '???'))
        month_pt = translate_month(month_raw)
        price_float = float(p.get('price', 0))
        
        # Format change string
        if change_val > 0:
            chg_str = f"+{change_val:.2f}"
            pct_str = f"(+{pct_val:.2f}%)"
        elif change_val < 0:
            chg_str = f"{change_val:.2f}"
            pct_str = f"({pct_val:.2f}%)"
        else:
            chg_str = ""
            pct_str = ""
        
        # Format line
        if change_val == 0:
            stats = "Estável"
        else:
            stats = f"{chg_str} {pct_str}"
            
        lines.append(f"• *IO Swap {month_pt}*")
        lines.append(f"`${price_float:.2f}`   |  {stats}")
        
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

        # 4. Send Messages via DeliveryReporter
        from execution.integrations.uazapi_client import UazapiClient
        uazapi = UazapiClient()

        delivery_contacts = [bc for c in contacts if (bc := build_contact_from_row(c))]

        if args.dry_run:
            logger.info(f"[DRY RUN] Would send to {len(delivery_contacts)} contacts")
            return

        reporter = DeliveryReporter(
            workflow="daily_report",
            send_fn=uazapi.send_message,
            gh_run_id=os.getenv("GITHUB_RUN_ID"),
        )
        report = reporter.dispatch(delivery_contacts, message)
        logger.info(
            f"Daily report broadcast complete. Sent: {report.success_count}, "
            f"Failed: {report.failure_count}"
        )

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
