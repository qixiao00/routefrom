"""Ordered, checksummed SQL migrations on a direct Postgres connection."""
from __future__ import annotations

import argparse
import hashlib
import os
import re
from pathlib import Path


def migrate(database_url: str, directory: Path, *, database_name: str | None = None) -> list[str]:
    import psycopg
    from psycopg import sql

    options = {"dbname": database_name} if database_name else {}
    applied: list[str] = []
    with psycopg.connect(database_url, autocommit=True, **options) as connection:
        if "-pooler" in (connection.info.host or ""):
            raise ValueError("migrations require a direct connection")
        connection.execute("SELECT pg_advisory_lock(726688004)")
        try:
            connection.execute("CREATE SCHEMA IF NOT EXISTS routefrom_meta")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS routefrom_meta.schema_migrations (
                     filename text PRIMARY KEY, sha256 text NOT NULL,
                     applied_at timestamptz NOT NULL DEFAULT now())"""
            )
            for path in sorted(directory.glob("*.sql")):
                content = path.read_text(encoding="utf-8").replace("\r\n", "\n")
                digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                existing = connection.execute(
                    "SELECT sha256 FROM routefrom_meta.schema_migrations WHERE filename = %s",
                    (path.name,),
                ).fetchone()
                if existing:
                    if existing[0] != digest:
                        raise ValueError(f"applied migration was modified: {path.name}")
                    continue
                if not re.search(r"COMMIT;\s*$", content):
                    raise ValueError(f"migration requires a terminal COMMIT: {path.name}")
                ledger = sql.SQL(
                    "INSERT INTO routefrom_meta.schema_migrations (filename, sha256) VALUES ({}, {});"
                ).format(sql.Literal(path.name), sql.Literal(digest)).as_string(connection)
                script = re.sub(r"COMMIT;\s*$", lambda _, entry=ledger: entry + "\nCOMMIT;", content)
                try:
                    connection.execute(script, prepare=False)
                except Exception:
                    connection.execute("ROLLBACK")
                    raise
                applied.append(path.name)
        finally:
            connection.execute("SELECT pg_advisory_unlock(726688004)")
    return applied


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply checksummed RouteFrom SQL migrations.")
    parser.add_argument("--directory", type=Path,
                        default=Path(__file__).resolve().parents[3] / "db" / "migrations")
    parser.add_argument("--database-name")
    args = parser.parse_args()
    database_url = os.environ.get("DATABASE_URL_DIRECT")
    if not database_url:
        parser.error("DATABASE_URL_DIRECT is required")
    applied = migrate(database_url, args.directory, database_name=args.database_name)
    print(f"Applied {len(applied)} migrations: {', '.join(applied) or 'already current'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
