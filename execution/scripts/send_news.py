
#!/usr/bin/env python3
import asyncio
import sys
import os
import argparse

# Add project root to path
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from execution.integrations.contacts_repo import ContactsRepo
from execution.integrations.uazapi_client import UazapiClient
from execution.core.event_bus import with_event_bus, get_current_bus
from execution.core.logger import WorkflowLogger
from execution.core.delivery_reporter import DeliveryReporter, build_delivery_contact
from execution.core.progress_reporter import ProgressReporter


@with_event_bus("market_news")
def main():
    logger = WorkflowLogger("SendNews")
    bus = get_current_bus()
    bus.emit("step", label="Iniciando send_news")
    parser = argparse.ArgumentParser()
    parser.add_argument("--message", help="Message text to send")
    parser.add_argument("--file", help="Path to text file containing message")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--workflow",
        default="manual_news",
        help="Workflow label used in progress/delivery reports (e.g. market_news, rationale_news)",
    )
    args = parser.parse_args()

    workflow_name = args.workflow

    progress = ProgressReporter(
        workflow=workflow_name,
        chat_id=os.getenv("TELEGRAM_CHAT_ID"),
        gh_run_id=os.getenv("GITHUB_RUN_ID"),
    )
    progress.start("Preparando dados...")

    try:
        if args.file:
            with open(args.file, 'r', encoding='utf-8') as f:
                msg = f.read()
        elif args.message:
            msg = args.message
        else:
            logger.critical("Either --message or --file is required")
            progress.finish_empty("falha na ingestao")
            sys.exit(1)

        # 1. Fetch Contacts
        logger.info("Fetching contacts...")
        bus.emit("step", label="Buscando contatos")
        try:
            contacts_repo = ContactsRepo()
            contacts = contacts_repo.list_by_list_code("minerals_report")
        except Exception as e:
            logger.critical(f"Failed to fetch contacts: {e}")
            progress.finish_empty("falha na ingestao")
            sys.exit(1)

        if not contacts:
            logger.warning("No contacts found.")
            progress.finish_empty("nenhum contato ativo")
            sys.exit(0)

        # 2. Send via DeliveryReporter
        uazapi = UazapiClient()

        delivery_contacts = [build_delivery_contact(c) for c in contacts]

        if not delivery_contacts:
            logger.warning("No valid delivery contacts after filtering.")
            progress.finish_empty("nenhum contato ativo")
            sys.exit(0)

        if args.dry_run:
            logger.info(f"[DRY RUN] Would send to {len(delivery_contacts)} contacts")
            progress.finish_empty("dry-run")
            return

        progress.update(f"Enviando pra {len(delivery_contacts)} contatos... (0/{len(delivery_contacts)})")

        bus.emit("step", label=f"Enviando WhatsApp para {len(delivery_contacts)} contatos")
        reporter = DeliveryReporter(
            workflow=workflow_name,
            send_fn=uazapi.send_message,
            notify_telegram=False,
            gh_run_id=os.getenv("GITHUB_RUN_ID"),
        )
        report = reporter.dispatch(
            delivery_contacts,
            msg,
            on_progress=progress.on_dispatch_tick,
        )
        asyncio.run(progress.finish(report, message=msg))
        logger.info(
            f"News broadcast complete. Sent: {report.success_count}, "
            f"Failed: {report.failure_count}"
        )

    except Exception as exc:
        progress.fail(exc)
        raise

if __name__ == "__main__":
    main()
