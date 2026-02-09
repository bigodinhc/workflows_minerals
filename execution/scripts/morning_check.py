#!/usr/bin/env python3
"""
Morning Data Check & Report (08:30 - 10:00)
Checks for daily Platts data, formats a hybrid message (Bold + Text),
sends via WhatsApp, and ensures single execution per day via Control Sheet.
"""

import os
import sys
import time
import argparse
from datetime import datetime, date

# Adjust path to allow imports from root
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from execution.core.logger import WorkflowLogger
from execution.integrations.platts_client import PlattsClient
from execution.integrations.sheets_client import SheetsClient
from execution.integrations.uazapi_client import UazapiClient

# --- CONFIGURATION (Whitelists ported from JS) ---

# --- CONFIGURATION (Whitelists using Variable Keys for stability) ---

# --- CONFIGURATION (Whitelists using Symbols) ---

FINES_KEYS = [
    "IOBBA00", # Brazilian Blend Fines CFR Qingdao
    "IODFE00", # IO fines Fe 58%
    "IOPRM00", # IO fines Fe 65%
    "IOJBA00", # Jimblebar Fines CFR Qingdao
    "IOMAA00", # Mining Area C Fines CFR Qingdao
    "IONHA00", # Newman High Grade Fines CFR Qingdao
    "IOPBQ00", # Pilbara Blend Fines CFR Qingdao
    "IODBZ00", # IODEX CFR CHINA 62% Fe
    "TS01021", # TSI Iron Ore Fines 62% Fe CFR China
]

LUMP_PELLET_KEYS = [
    "IODRP00", # Iron Ore 67.5% Fe DR Pellet Premium
    "IOCQR04", # Iron Ore Blast Furnace 63% Fe Pellet CFR China
    "IOBFC04", # Iron Ore Blast Furnace Pellet Premium CFR China Wkly
    "IOCLS00", # Iron Ore Lump Outright Price CFR China
]

VIU_KEYS = [
    "IOALE00", # Alumina Diff 2.5-4%
    "TSIAF00", # Alumina Diff <5% (55-60% Fe)
    "TSIAD00", # Fe Diff
    "IOPPQ00", # Phos Diff 0.09-0.12%
    "IOPPT00", # Phos Diff 0.10-0.11%
    "IOPPU00", # Phos Diff 0.11-0.12%
    "IOPPV00", # Phos Diff 0.12-0.15%
    "IOALF00", # Silica Diff 3-4.5%
    "TSIAI00", # Silica Diff 55-60% Fe
    "IOADF10", # Alumina Diff 1-2.5%
    "IOPPS10", # Silica Diff 4.5-6.5%
    "IOPPS20", # Silica Diff 6.5-9%
    "IOMGD00", # Mid Range Diff 60-63.5 Fe
]

FREIGHT_KEYS = [] # No freight symbols mapped yet

FREIGHT_KEYS = [] # No freight mapped

REPORT_TYPE = "MORNING_REPORT"
SHEET_ID = "1tU3Izdo21JichTXg15bc1paWUiN8XioJYZUPpbIUgL0"
SHEET_NAME_CONTACTS = "Página1"

def normalize_text(text):
    if not text: return ""
    return " ".join(str(text).strip().lower().split())

def desc_keys(item):
    """Generates normalized keys for matching (description and description+unit)."""
    desc = normalize_text(item.get('product', ''))
    unit = normalize_text(item.get('unit', ''))
    keys = [desc]
    if unit and unit not in desc:
        keys.append(normalize_text(f"{desc} {unit}"))
    return keys

def format_line(item):
    """Formats a single item line: Bold name + backtick price"""
    if not item: return None
    
    desc = item.get('product', 'Unknown')
    # Cleanup assess type if needed (e.g. remove redundant text)
    assess_type = item.get('assessmentType', '').strip()
    # If unit is basically the same as assess type, simplify
    if assess_type.lower() == item.get('unit', '').lower():
        assess_type = item.get('unit', '')
        
    price = item['price']
    change = item.get('change', 0)
    pct = item.get('changePercent', 0)
    
    # Logic for Indicators & Color
    if change > 0:
        sign_str = f"+{change:.2f}"
        pct_str = f"(+{pct:.2f}%)"
    elif change < 0:
        sign_str = f"{change:.2f}" # change is negative
        pct_str = f"({pct:.2f}%)"
    else:
        sign_str = ""
        pct_str = ""
        
    # Clean Format with single backticks:
    # • *Product Name*
    # `$100.00`   |  +0.50 (+0.50%)
    
    price_fmt = f"${price:.2f}"
    
    if change == 0:
         stats_block = "Estável"
    else:
         stats_block = f"{sign_str} {pct_str}"
         
    return f"• *{desc}*\n`{price_fmt}`   |  {stats_block}"

def filter_by_keys(items, keys_whitelist):
    """Filters items matching whitelist KEYS (more stable than description)."""
    results = []
    # Convert list to set for O(1)
    wanted = set(keys_whitelist)
    
    for item in items:
        # Check if variable_key matches
        if item.get('variable_key') in wanted:
            line = format_line(item)
            if line: results.append(line)
            
    return results

def get_section(title, lines):
    if not lines: return None
    joined = "\n".join(lines)
    return f"{title}\n{joined}"

def build_message(report_items, date_str):
    """
    Orchestrates the message construction with sections.
    """
    # Filter by KEYS logic
    fines_lines = filter_by_keys(report_items, FINES_KEYS)
    lump_lines = filter_by_keys(report_items, LUMP_PELLET_KEYS)
    viu_lines = filter_by_keys(report_items, VIU_KEYS)
    freight_lines = filter_by_keys(report_items, FREIGHT_KEYS)
    
    # Fallback/Hints deprecated for now as we only have 8 specific keys mapped
    # If customer adds more keys to PlattsClient.SYMBOLS_MAPPING later, add them to lists above.
    
    # Build parts
    header = f"📊 *MINERALS TRADING DAILY REPORT* 📊\n🔍 *IRON ORE MARKET UPDATE* - {date_str}"
    parts = [header]
    
    s1 = get_section("🪨 *FINES*", fines_lines)
    if s1: parts.extend(["", s1])
    
    s2 = get_section("🧱 *LUMP AND PELLET*", lump_lines)
    if s2: parts.extend(["", s2])
    
    s3 = get_section("🧪 *VIU DIFFERENTIALS*", viu_lines)
    if s3: parts.extend(["", s3])
    
    s4 = get_section("🚢 *FREIGHT*", freight_lines)
    if s4: parts.extend(["", s4])
    
    return "\n".join(parts)


def main():
    logger = WorkflowLogger("MorningCheck")
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Skip sending and saving state")
    parser.add_argument("--date", type=str, help="Override date (YYYY-MM-DD)", default=None)
    args = parser.parse_args()
    
    # 1. Check Date (Business Day)
    if args.date:
        try:
            today = datetime.strptime(args.date, "%Y-%m-%d").date()
            logger.info(f"Using manual date override: {today}")
        except ValueError:
            logger.error("Invalid date format. Use YYYY-MM-DD.")
            sys.exit(1)
    else:
        today = date.today()

    date_str = today.strftime("%Y-%m-%d")
    date_fmt_br = today.strftime("%d/%m/%Y")
    
    logger.info(f"Starting Morning Check for {date_str}")
    
    # 2. Check Control Sheet
    sheets = SheetsClient()
    
    if not args.dry_run:
        if sheets.check_daily_status(SHEET_ID, date_str, REPORT_TYPE):
            logger.info("Report already sent today. Exiting.")
            return

    # 3. Fetch Data
    platts = PlattsClient()
    # We use today for fetching. The client handles prev day calculation.
    report_items = platts.get_report_data(datetime.combine(today, datetime.min.time()))
    
    if not report_items:
        logger.info("No data available yet from Platts. Will retry later.")
        sys.exit(0) # Exit success (so GitHub Action doesn't fail, just finishes)
    
    # --- VALIDATION: Check minimum items collected ---
    MIN_ITEMS_EXPECTED = 10  # Threshold - should collect at least 10 symbols
    TOTAL_SYMBOLS = 26  # Total configured in SYMBOLS_DETAILS
    
    logger.info(f"Items collected: {len(report_items)}/{TOTAL_SYMBOLS}")
    
    if len(report_items) < MIN_ITEMS_EXPECTED:
        logger.warning(f"⚠️ INCOMPLETE DATA: Only {len(report_items)}/{TOTAL_SYMBOLS} items collected!")
        logger.warning(f"   Threshold is {MIN_ITEMS_EXPECTED}. Report may be incomplete.")
        # Note: Still sending for now, but user is warned
        
    # DEBUG: Print items to see why filtering failed
    if args.dry_run:
        logger.info("--- DEBUG: RAW ITEMS FROM PLATTS ---")
        for i in report_items:
            logger.info(f"Item: {i}")
        logger.info("------------------------------------")

    # 4. Format Message
    message = build_message(report_items, date_fmt_br)
    
    logger.info("Message formatted.")
    if args.dry_run:
        print("\n--- MESSAGE PREVIEW ---\n")
        print(message)
        print("\n-----------------------\n")
        
    # 5. Send & Mark
    contacts = sheets.get_contacts(SHEET_ID, SHEET_NAME_CONTACTS)
    
    if not contacts:
        logger.warning("No contacts found.")
        return
        
    uazapi = UazapiClient()
    success_count = 0
    
    for contact in contacts:
        # Same phone logic from send_daily_report.py
        raw_phone = (
            contact.get('Evolution-api') or 
            contact.get('Telefone') or 
            contact.get('Phone') or 
            contact.get('From')
        )
        
        if not raw_phone: continue
        phone = str(raw_phone).replace("whatsapp:", "").strip()
        
        # Check Big Filter (if applicable? User said "same flow and list")
        # In Implementation Plan we didn't specify filter, but Task.md says "Filter 'Big'".
        # User said "Queremos esse mesmo fluxo... e a mesma lista".
        # Assuming we apply "Big" filter logic if present in sheet.
        # Check logic in SheetsClient.get_contacts -> IT ALREADY FILTERS 'Big'! 
        # So contacts list is already filtered.
        
        if args.dry_run:
            logger.info(f"[DRY RUN] Would send to {phone}")
        else:
            try:
                uazapi.send_message(phone, message)
                success_count += 1
                time.sleep(2)
            except Exception as e:
                logger.error(f"Failed to send to {phone}", {"error": str(e)})
                
    logger.info(f"Broadcast complete. Sent: {success_count}")
    
    if not args.dry_run and success_count > 0:
        sheets.mark_daily_status(SHEET_ID, date_str, REPORT_TYPE)
        logger.info("Control sheet updated.")

if __name__ == "__main__":
    main()
