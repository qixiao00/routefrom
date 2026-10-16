"""Operator CLI for the archive foundation; the authenticated website workflow is separate."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from uuid import UUID

from routefrom_pipeline.backup_archive import verify_backup
from routefrom_pipeline.backup_manifest import BackupError


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Create or verify a private RouteFrom backup archive."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser(
        "verify", help="verify archive and exact source evidence without a database"
    )
    verify.add_argument("archive", type=Path)
    export = commands.add_parser(
        "export", help="operator-only export, using existing DATABASE_URL_DIRECT"
    )
    export.add_argument("--user-id", type=UUID, required=True)
    export.add_argument("--library-id", type=UUID, required=True)
    export.add_argument("--data-dir", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--database-name")
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            result = verify_backup(args.archive)
        else:
            import psycopg

            from routefrom_pipeline.database.backups import export_library_backup

            url = os.environ.get("DATABASE_URL_DIRECT")
            if not url:
                parser.error("DATABASE_URL_DIRECT is required")
            options = {"dbname": args.database_name} if args.database_name else {}
            with psycopg.connect(url, autocommit=True, **options) as connection:
                result = export_library_backup(
                    connection,
                    user_id=args.user_id,
                    library_id=args.library_id,
                    data_root=args.data_dir,
                    output=args.output,
                )
        print(
            json.dumps(
                {
                    "verified": True,
                    "formatVersion": 1,
                    "files": len(result.manifest["files"]),
                    "notes": len(result.manifest["notes"]),
                    "views": len(result.manifest["views"]),
                    "pointRows": sum(result.point_counts.values()),
                    "sha256": result.archive_sha256,
                }
            )
        )
        return 0
    except BackupError as error:
        print(f"Backup rejected: {error}", file=sys.stderr)
    except Exception as error:  # noqa: BLE001 -- sanitize errors at the operator CLI boundary
        # Database/filesystem messages can contain hosts, private keys or paths.
        print(
            f"Backup failed ({type(error).__name__}); check private storage and database access.",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
