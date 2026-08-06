from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from uuid import UUID

from routefrom_pipeline.orchestration import run_linggan_pipeline


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="routefrom-process",
        description="Import and process one registered Linggan footprint dataset.",
    )
    parser.add_argument("--dataset-id", type=UUID, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-object-key")
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--code-revision")
    parser.add_argument("--import-batch-size", type=int, default=2_000)
    parser.add_argument("--no-activate", action="store_true")
    return parser


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    database_url = os.environ.get("DATABASE_URL_DIRECT")
    if not database_url:
        parser.error("DATABASE_URL_DIRECT is required")
    try:
        import psycopg
    except ImportError:
        parser.error('database support is missing; install with pip install -e ".[database]"')

    with psycopg.connect(database_url) as connection:
        result = run_linggan_pipeline(
            connection,
            dataset_id=args.dataset_id,
            source_path=args.source,
            source_object_key=args.source_object_key,
            timezone=args.timezone,
            code_revision=args.code_revision,
            activate=not args.no_activate,
            import_batch_size=args.import_batch_size,
        )
    print(
        json.dumps(
            {
                "datasetImportId": str(result.dataset_import.dataset_import_id),
                "importJobId": str(result.import_job_id),
                "pointCount": result.dataset_import.profile.row_count,
                "processingRunId": str(result.processing.processing_run_id),
                "reusedImport": result.dataset_import.reused,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
