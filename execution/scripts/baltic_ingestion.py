
#!/usr/bin/env python3
"""
Baltic Index Ingestion & Reporting
Automates:
1. Fetch latest "Exchange" email from Outlook (Graph API)
2. Download PDF Attachment
3. Extract data using Anthropic Claude
4. Ingest into IronMarket API
5. Send WhatsApp Report
"""

import os
import sys
import argparse
import requests
import json
from datetime import datetime

# Adjust path to allow imports from root
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from execution.core.logger import WorkflowLogger
from execution.integrations.baltic_client import BalticClient
from execution.integrations.claude_client import ClaudeClient
from execution.integrations.sheets_client import SheetsClient
from execution.integrations.uazapi_client import UazapiClient

# CONFIGURATION
REPORT_TYPE = "BALTIC_REPORT"
SHEET_ID = "1tU3Izdo21JichTXg15bc1paWUiN8XioJYZUPpbIUgL0"
SHEET_NAME_CONTACTS = "Página1"
IRONMARKET_URL = "https://merry-adaptation-production.up.railway.app/ingest/price"
IRONMARKET_API_KEY = "ironmkt_WUbuYLe4m06GTiYos_fVwvBfNa2l8GWoJtE9K8MJFCY" # Keeping hardcoded as requested, or load from env

def get_emoji(direction):
    if direction == 'UP': return '📈'
    if direction == 'DOWN': return '📉'
    return '➡️'

def format_change(change, decimals=0):
    if not change: return "0"
    try:
        val = float(change)
        sign = "+" if val > 0 else ""
        fmt = f"{{:.{decimals}f}}"
        return f"{sign}{fmt.format(val)}"
    except:
        return str(change)

def route_emoji(change):
    try:
        val = float(change)
        if val > 0: return '📈'
        if val < 0: return '📉'
    except:
        pass
    return '➡️'

def format_whatsapp_message(data):
    """Formats the data into the morning check style layout."""
    from datetime import datetime
    
    # Helper to safe get
    def get_route(code):
        for r in data.get('routes', []):
            if r.get('code') == code: return r
        return {}

    c2 = get_route('C2')
    c3 = get_route('C3')
    c5 = get_route('C5')
    c7 = get_route('C7')
    c8 = get_route('C8')
    c5tc = get_route('C5TC')
    
    # Safely get values
    bdi = data.get('bdi', {})
    capesize = data.get('capesize', {})
    panamax = data.get('panamax', {})
    supramax = data.get('supramax', {})
    handysize = data.get('handysize', {})
    
    # Format date as DD/MM/YYYY
    report_date = data.get('report_date', '')
    try:
        dt = datetime.strptime(report_date, '%Y-%m-%d')
        date_formatted = dt.strftime('%d/%m/%Y')
    except:
        date_formatted = report_date
    
    def format_line(name, value, change, unit="", decimals=2, is_index=False):
        """Format a single data line in the style of morning check."""
        if is_index:
            val_str = f"{int(value)}" if value else "N/A"
            chg_str = format_change(change, 0)
        else:
            val_str = f"${value:.{decimals}f}" if value else "N/A"
            chg_str = format_change(change, decimals)
        
        # Calculate percentage if possible
        if value and change:
            try:
                pct = (float(change) / (float(value) - float(change))) * 100
                pct_str = f"({pct:+.2f}%)"
            except:
                pct_str = ""
        else:
            pct_str = ""
        
        if change == 0 or not change:
            status = "Estável"
            return f"• *{name}*\n`{val_str}{unit}`   |  {status}"
        else:
            return f"• *{name}*\n`{val_str}{unit}`   |  {chg_str} {pct_str}"
    
    lines = []
    
    # Header
    lines.append("📊 *MINERALS TRADING DAILY REPORT* 📊")
    lines.append(f"🚢  BALTIC EXCHANGE UPDATE - {date_formatted}")
    lines.append("")
    
    # BDI Section
    lines.append("🌊 *BALTIC DRY INDEX*")
    lines.append(format_line("BDI", bdi.get('value'), bdi.get('change'), "", 0, is_index=True))
    lines.append("")
    
    # Capesize Routes
    lines.append("⚓ *ROTAS CAPESIZE*")
    if c3.get('value'):
        lines.append(format_line("C3 Tubarao → Qingdao", c3.get('value'), c3.get('change'), "/ton"))
    if c5.get('value'):
        lines.append(format_line("C5 W.Australia → Qingdao", c5.get('value'), c5.get('change'), "/ton"))
    if c2.get('value'):
        lines.append(format_line("C2 Tubarao → Rotterdam", c2.get('value'), c2.get('change'), "/ton"))
    if c7.get('value'):
        lines.append(format_line("C7 Bolivar → Rotterdam", c7.get('value'), c7.get('change'), "/ton"))
    if c5tc.get('value'):
        lines.append(format_line("C5TC Timecharter Avg", c5tc.get('value'), c5tc.get('change'), "/day", 0, is_index=True))
    lines.append("")
    
    # Ship Type Indices
    lines.append("🚢 *INDICES POR TIPO*")
    lines.append(format_line("Capesize (100k+ DWT)", capesize.get('value'), capesize.get('change'), "", 0, is_index=True))
    lines.append(format_line("Panamax (60-80k DWT)", panamax.get('value'), panamax.get('change'), "", 0, is_index=True))
    lines.append(format_line("Supramax (45-60k DWT)", supramax.get('value'), supramax.get('change'), "", 0, is_index=True))
    lines.append(format_line("Handysize (15-35k DWT)", handysize.get('value'), handysize.get('change'), "", 0, is_index=True))
    
    return "\n".join(lines)

def ingest_to_ironmarket(data):
    """Sends C3 route to IronMarket API."""
    # Find C3
    c3 = None
    for r in data.get('routes', []):
        if r.get('code') == 'C3':
            c3 = r
            break
            
    if not c3:
        return False, "C3 Route not found"
        
    payload = {
        "variable_key": "FREIGHT_C3_BALTIC",
        "value": c3.get('value'),
        "source": "baltic_morning_email"
    }
    
    headers = {
        "X-API-Key": os.getenv("IRONMARKET_API_KEY", IRONMARKET_API_KEY),
        "Content-Type": "application/json"
    }
    
    try:
        res = requests.post(IRONMARKET_URL, json=payload, headers=headers)
        res.raise_for_status()
        return True, "Success"
    except Exception as e:
        return False, str(e)

def main():
    logger = WorkflowLogger("BalticIngestion")
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Skip sending and saving state")
    args = parser.parse_args()
    
    # 1. Check Control Sheet
    sheets = SheetsClient()
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    if not args.dry_run:
        if sheets.check_daily_status(SHEET_ID, today_str, REPORT_TYPE):
            logger.info("Baltic report already processed today. Exiting.")
            return

    # 2. Fetch Email & PDF
    logger.info("Checking Outlook for Baltic Exchange email...")
    baltic = BalticClient()
    
    try:
        msg = baltic.find_latest_email()
    except Exception as e:
        logger.error(f"Failed to fetch emails: {e}")
        sys.exit(1)
        
    if not msg:
        logger.info("No matching email found in the last 24h.")
        sys.exit(0)
    
    # NEW: Validate if email is actually from TODAY
    # Graph API returns UTC ISO string: 2026-02-05T16:00:00Z
    # We want to ensure we don't re-process yesterday's email if today's hasn't arrived
    email_date_str = msg['receivedDateTime']
    try:
        # Parse ISO format (handle Z if present)
        email_date_str_clean = email_date_str.replace("Z", "+00:00")
        email_dt = datetime.fromisoformat(email_date_str_clean).date()
        today_dt = datetime.utcnow().date()
        
        if email_dt != today_dt:
            logger.info(f"Found email but it is from {email_dt} (not today {today_dt}). Report not released yet.")
            logger.info(f"Subject: {msg['subject']}")
            sys.exit(0)
            
    except Exception as e:
        logger.warning(f"Could not validate email date: {e}. Proceeding with caution.")

    logger.info(f"Found email: {msg['subject']} ({msg['receivedDateTime']})")
    
    pdf_bytes, filename = baltic.get_pdf_attachment(msg['id'])
    
    if not pdf_bytes:
        logger.warning("No PDF attachment found in the email.")
        sys.exit(0)
        
    logger.info(f"Downloaded PDF: {filename}")
    
    # 3. Extract Data (Claude)
    logger.info("Sending to Claude for extraction...")
    claude = ClaudeClient()
    data = claude.extract_data_from_pdf(pdf_bytes)
    
    if not data or data.get('extraction_confidence') == 'low':
        logger.error("Extraction failed or low confidence.")
        # We could notify admin here
        sys.exit(1)
        
    logger.info(f"Extraction successful. Date: {data.get('report_date')}")
    
    if args.dry_run:
        print(json.dumps(data, indent=2))
        
    # 4. Ingest to IronMarket
    if not args.dry_run:
        success, err = ingest_to_ironmarket(data)
        if success:
            logger.info("Ingested C3 to IronMarket API.")
        else:
            logger.error(f"IronMarket Ingestion Failed: {err}")
            
    # 5. Send WhatsApp
    message = format_whatsapp_message(data)
    
    if args.dry_run:
        print("\n--- WHATSAPP PREVIEW ---\n")
        print(message)
    else:
        contacts = sheets.get_contacts(SHEET_ID, SHEET_NAME_CONTACTS)
        uazapi = UazapiClient()
        count = 0
        
        for contact in contacts:
            phone = contact.get('Evolution-api')
            if not phone: continue
            phone = str(phone).replace("whatsapp:", "").strip()
            
            try:
                uazapi.send_message(phone, message)
                count += 1
            except Exception as e:
                logger.error(f"Failed send to {phone}: {e}")
                
        logger.info(f"Sent to {count} contacts.")
        
        # 6. Mark Complete
        if count > 0:
            sheets.mark_daily_status(SHEET_ID, today_str, REPORT_TYPE)
            logger.info("Marked as complete in control sheet.")

if __name__ == "__main__":
    main()
