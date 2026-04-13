
#!/usr/bin/env python3
import sys
import os
import argparse

# Add project root to path
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from execution.integrations.sheets_client import SheetsClient
from execution.integrations.uazapi_client import UazapiClient
from execution.core.logger import WorkflowLogger
from execution.core.delivery_reporter import DeliveryReporter, Contact

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
        
    # 2. Send via DeliveryReporter
    uazapi = UazapiClient()

    def build_contact(c):
        raw_phone = (
            c.get('Evolution-api') or c.get('Telefone') or
            c.get('Phone') or c.get('From')
        )
        if not raw_phone:
            return None
        phone = str(raw_phone).replace("whatsapp:", "").strip()
        name = c.get("Nome") or c.get("Name") or "—"
        return Contact(name=name, phone=phone)

    delivery_contacts = [bc for c in contacts if (bc := build_contact(c))]

    if args.dry_run:
        logger.info(f"[DRY RUN] Would send to {len(delivery_contacts)} contacts")
        return

    reporter = DeliveryReporter(
        workflow="manual_news",
        send_fn=uazapi.send_message,
        gh_run_id=os.getenv("GITHUB_RUN_ID"),
    )
    report = reporter.dispatch(delivery_contacts, msg)
    logger.info(
        f"Manual news broadcast complete. Sent: {report.success_count}, "
        f"Failed: {report.failure_count}"
    )

if __name__ == "__main__":
    main()
