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

FINES_WHITELIST_KEYS = [
    "PLATTS_IODEX_62_CFR_CHINA",
    "PLATTS_VIU_FE_60_63", # Check if this belongs here or VIU
    # Add other keys based on PlattsClient SYMBOLS_MAPPING
    # Wait, SYMBOLS_MAPPING in PlattsClient only has 8 keys!
    # The original JS checklist had ~30 items.
    # The user's fetch_platts.py ONLY has 8 keys mapped.
    # This means we can ONLY report on these 8 keys currently.
    
    # Let's map the available 8 keys to their sections:
    "PLATTS_IODEX_62_CFR_CHINA", # Fines
]

# Mapping based on typical classification:
# FINES: IODEX 62
# LUMP/PELLET: None in the current 8 keys?
# VIU: Silica, Alumina, Penalty, VIU Fe 60.63

# Re-reading SYMBOLS_MAPPING in platts_client.py:
# "PLATTS_IODEX_62_CFR_CHINA": "IODBZ00", -> FINES
# "PLATTS_VIU_FE_60_63": "IOMGD00", -> VIU ?
# "PLATTS_SILICA_3_4P5": "IOALF00", -> VIU
# "PLATTS_SILICA_4P5_6P5": "IOPPS10", -> VIU
# "PLATTS_SILICA_6P5_9": "IOPPS20", -> VIU
# "PLATTS_ALUMINA_1_2P5": "IOADF10", -> VIU
# "PLATTS_ALUMINA_2P5_4": "IOALE00", -> VIU
# "PLATTS_P_PENALTY": "IOPPQ00", -> VIU

# So currently we only have coverage for 1 Fines item and 7 VIU items.
# The user's original JS template had way more.
# BUT we are limited by what is in fetch_platts.py (which I refactored to PlattsClient).
# The user said "use this fetch_platts.py code".
# So I must restrict the report to what is actually fetched.

FINES_KEYS = [
    "PLATTS_IODEX_62_CFR_CHINA"
]

LUMP_PELLET_KEYS = [] # No symbols mapped in fetch_platts.py for this yet

VIU_KEYS = [
    "PLATTS_VIU_FE_60_63",
    "PLATTS_SILICA_3_4P5",
    "PLATTS_SILICA_4P5_6P5",
    "PLATTS_SILICA_6P5_9",
    "PLATTS_ALUMINA_1_2P5",
    "PLATTS_ALUMINA_2P5_4",
    "PLATTS_P_PENALTY"
]

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
    """Formats a single item line: Bold logic + Prices"""
    if not item: return None
    
    desc = item.get('product', 'Unknown')
    # Cleanup assess type if needed (e.g. remove redundant text)
    assess_type = item.get('assessmentType', '').strip()
    # If unit is basically the same as assess type, simplify
    if assess_type.lower() == item.get('unit', '').lower():
        assess_type = item.get('unit', '')
        
    price = f"${item['price']:.2f}"
    
    change = item.get('change', 0)
    pct = item.get('changePercent', 0)
    
    delta_str = "sem alteração"
    
    # Logic from JS:
    # if (Number.isFinite(ch) && ch !== 0) ...
    
    if change != 0:
        sign = "+" if change >= 0 else "-" # JS uses signedMoney logic which handles abs
        # Note: In Python f"{change:+.2f}" adds sign automatically
        price_change_str = f"{change:+.2f}"
        pct_change_str = f"{pct:+.2f}%" if pct != 0 else ""
        
        delta_str = f"${price_change_str}"
        if pct_change_str:
            delta_str += f" ({pct_change_str})"
            
    elif pct != 0:
         delta_str = f"{pct:+.2f}%"
         
    # Final Line Format
    # - *ProductName*
    #   $Price Unit Changestring
    
    unit_display = f" {assess_type}" if assess_type else ""
    return f"- *{desc}*\n  {price}{unit_display} {delta_str}"

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
    
    s1 = get_section("📈 *FINES*", fines_lines)
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
