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

FINES_WHITELIST = [
    'IODEX CFR CHINA 62% Fe $/DMt',
    'IO fines Fe 65% $/DMt',
    'IO fines Fe 58% $/DMt',
    'TSI Iron Ore Fines 62% Fe CFR China',
    'Mining Area C Fines CFR Qingdao $/DMT',
    'Pilbara Blend Fines CFR Qingdao $/DMT',
    'Brazilian Blend Fines CFR Qingdao $/DMT',
    'Jimblebar Fines CFR Qingdao $/DMT',
    'Newman High Grade Fines CFR Qingdao $/DMT',
]

LUMP_PELLET_WHITELIST = [
    'Iron Ore Spot Lump Premium China $/dmtu',
    'Iron Ore Blast Furnace Pellet Premium CFR China $/DMT Wkly',
    'Iron Ore Blast Furnace 63% Fe Pellet CFR China $/DMT',
    'Atlantic Basin Iron Ore Pellets Contract Price Brazil Export FOB Monthly',
    'Iron Ore 67.5% Fe DR Pellet Premium (62% Fe basis) $/DMT Mthly',
    'Iron Ore Lump Outright Price CFR China',
]

VIU_WHITELIST = [
    'Iron Ore Silica Differential per 1% with 3-4.5% range $/DMT',
    'Iron Ore Alumina Differential per 1% with 2.5-4% $/DMT',
    'Iron Ore Fe Differential per 1% Fe within 55-60% Fe Fines',
    'Iron Ore Phosphorus Differential per 0.01% for 0.10%-0.11% $/DMT',
    'Iron Ore Silica Differential per 1% within 55-60% Fe Fines',
    'Iron ore Alumina differential per 1% within 1-2.5% $/DMT',
    'Iron Ore Phosphorus Differential per 0.01% for 0.11%-0.12% $/DMT',
    'Iron Ore Phosphorus Differential per 0.01% for 0.09%-0.12% $/DMT',
    'Mid Range Diff 60-63.5 Fe $/DMt',
    'Iron Ore Alumina Differential per 1% within <5% (55-60% Fe Fines)',
    'Iron Ore Phosphorus Differential per 0.01% for 0.12%-0.15% $/DMT',
    'Iron ore Silica differential per 1% within 6.5-9% $/DMT',
    'Iron ore Silica differential per 1% within 4.5-6.5% $/DMT',
]

FREIGHT_WHITELIST = [
    'DBF Iron Ore Tubarao Brazil ECSA-Tubarao S Brazil-Qingdao N China 170kt $/mt Capesize',
    'DBF Iron Ore Western Australia-Qingdao N China 170kt $/mt Capesize',
    'DBF Iron Ore Saldanha Bay S Africa-Qingdao N China 170kt $/mt Capesize',
    'DBF Iron Ore Mormugao WC India-Qingdao N China 75kt $/mt Panamax',
    'DBF Iron Ore Paradip EC India-Qingdao N China 50kt $/mt Supramax',
    'DBF Iron Ore Port Cartier Canada-Rotterdam Netherlands 70kt $/mt Panamax',
    'DBF Iron Ore Seven Islands-Qingdao 170kt $/mt Capesize',
    'DBF Iron Ore Yuzhny Ukraine-Qingdao N China 160kt $/mt Capesize',
]

FREIGHT_FALLBACK_HINTS = [
    'brazil', 'tubarao', 'western australia', 'australia',
    'saldanha', 'south africa', 'africa do sul',
    'mormugao', 'paradip', 'port cartier', 'seven islands', 'qingdao', 'rotterdam'
]

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

def filter_by_whitelist(items, whitelist):
    """Filters items matching whitelist descriptions."""
    norm_whitelist = {normalize_text(w) for w in whitelist}
    results = []
    
    for item in items:
        # Check if any key matches
        keys = desc_keys(item)
        if any(k in norm_whitelist for k in keys):
            line = format_line(item)
            if line: results.append(line)
            
    return results

def filter_by_hints(items, hints):
    """Filters items containing hint substrings."""
    if not items: return []
    norm_hints = [normalize_text(h) for h in hints]
    results = []
    
    for item in items:
        desc = normalize_text(item.get('product', ''))
        if any(h in desc for h in norm_hints):
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
    # Separate items (Simulating the JS logic where inputs are separated objects)
    # Since our client returns a flat list, we filter by whitelist to categorize
    # This assumes items are available in the full list
    
    # Note: The JS logic had distinct inputs for Fines, Lump, etc.
    # Our client fetching ALL symbols. We need to categorize them.
    # Simplification: We iterate the full list against whitelists.
    
    # To avoid duplicates if an item matches multiple lists (unlikely but possible), 
    # lets process sequentially.
    
    fines_lines = filter_by_whitelist(report_items, FINES_WHITELIST)
    lump_lines = filter_by_whitelist(report_items, LUMP_PELLET_WHITELIST)
    viu_lines = filter_by_whitelist(report_items, VIU_WHITELIST)
    freight_lines = filter_by_whitelist(report_items, FREIGHT_WHITELIST)
    
    # Fallback for Freight hints if empty
    if not freight_lines:
        freight_lines = filter_by_hints(report_items, FREIGHT_FALLBACK_HINTS)
    
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
