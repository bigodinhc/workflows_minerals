#!/usr/bin/env python3
"""One-shot migration: rebuild global platts:seen from archives.

Usage:
    python execution/scripts/rebuild_dedup.py           # dry-run (default)
    python execution/scripts/rebuild_dedup.py --execute  # apply changes
"""
import argparse
import json
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from execution.core.event_bus import with_event_bus
from execution.curation.id_gen import generate_id


def rebuild(client, dry_run: bool = True) -> dict:
    """Rebuild global seen set from all archives.

    Returns dict with stats: archive_count, unique_ids, skipped, dated_keys_deleted.
    """
    archive_keys = list(client.scan_iter(match="platts:archive:*", count=500))
    now = time.time()
    new_ids: set[str] = set()
    skipped = 0

    for key in archive_keys:
        raw = client.get(key)
        if raw is None:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            skipped += 1
            continue
        title = data.get("title", "")
        if not title or not title.strip():
            skipped += 1
            continue
        try:
            new_id = generate_id(title)
            new_ids.add(new_id)
        except ValueError:
            skipped += 1

    dated_keys = list(client.scan_iter(match="platts:seen:????-??-??", count=100))

    result = {
        "archive_count": len(archive_keys),
        "unique_ids": len(new_ids),
        "skipped": skipped,
        "dated_keys_found": len(dated_keys),
    }

    if dry_run:
        print(f"[DRY RUN] Would ZADD {len(new_ids)} IDs into platts:seen")
        print(f"[DRY RUN] Would DEL {len(dated_keys)} dated seen keys: {dated_keys}")
        print(f"[DRY RUN] Archives scanned: {len(archive_keys)}, skipped: {skipped}")
        return result

    if new_ids:
        pipe = client.pipeline()
        for new_id in new_ids:
            pipe.zadd("platts:seen", {new_id: now})
        pipe.execute()

    for key in dated_keys:
        client.delete(key)

    print(f"[DONE] ZADD {len(new_ids)} IDs into platts:seen")
    print(f"[DONE] DEL {len(dated_keys)} dated seen keys")
    print(f"[DONE] Archives scanned: {len(archive_keys)}, skipped: {skipped}")
    return result


@with_event_bus("rebuild_dedup")
def main():
    parser = argparse.ArgumentParser(description="Rebuild global platts:seen from archives")
    parser.add_argument("--execute", action="store_true", help="Apply changes (default is dry-run)")
    args = parser.parse_args()

    from execution.curation import redis_client
    client = redis_client._get_client()
    rebuild(client, dry_run=not args.execute)


if __name__ == "__main__":
    main()
