# RouteFrom processing worker

`routefrom_pipeline` turns immutable Linggan observations into versioned,
explainable quality, continuity, stay, trip, mode, and trajectory results.

## Full import and processing job

Install the database extra and connect with the direct (non-pooled) database
URL. The dataset row must already be registered with the source SHA-256, byte
size, and owner before the worker starts.

```powershell
python -m pip install -e ".[database]"
```

```powershell
$env:DATABASE_URL_DIRECT = "postgresql://..."
routefrom-process `
  --dataset-id "00000000-0000-0000-0000-000000000000" `
  --source "C:\private\linggan.csv" `
  --code-revision "git-sha"
```

The command profiles and verifies the source, appends immutable location
points in batches, runs all explainable algorithms, persists a candidate
processing version, and activates it only after every stage succeeds. Import
jobs retain the source import and processing run IDs for audit and recovery.
Confirmed visits are clustered into versioned places with adaptive spatial
scales and probability-margin binding. The current place stage intentionally
does not claim building or POI identity without map evidence.
Nearby place fragments with overlapping observed positional support are
coalesced into one identity with a bounded 100 m group diameter; visits stay
immutable and the merged place's frequency is recomputed from all visits.
The worker also stores both cleaned and ENU Kalman/RTS-smoothed GPS variants.
Smoothing never crosses continuity gaps, locally falls back when it leaves the
measurement support radius, and only becomes preferred when run-level quality
checks pass.

Optional map matching uses a versioned local Valhalla/Meili graph. It splits
requests by supported mobility mode, never crosses an observation gap, and
keeps smoothed or cleaned GPS for every rejected interval. Remote URLs are
blocked by default so private traces are not sent to a public routing service.

```powershell
$env:ROUTEFROM_VALHALLA_URL = "http://127.0.0.1:8002"
$env:ROUTEFROM_MAP_SNAPSHOT_ID = "00000000-0000-0000-0000-000000000000"
$env:ROUTEFROM_MAP_SNAPSHOT_VERSION = "osm-2026-08-01"
$env:ROUTEFROM_MAP_DATASET_NAME = "china-regional-extract"
```

The snapshot ID must reference the exact graph artifact registered in
`app.map_data_snapshots`. Without all three required map settings, the worker
runs normally and keeps the GPS variants. A real local Valhalla graph has not
yet been installed or exercised in this repository; adapter and quality-gate
behavior are covered with deterministic fixtures.

## Building the private frontend preview

Until the PostGIS-backed workspace API is connected, the MVP can read a
gitignored JSON projection of a local CSV. The export runs the same processor,
keeps gaps and confidence-bearing derived events, and limits trajectory
vertices for an interactive MapLibre preview.

```powershell
routefrom-preview "C:\private\linggan.csv" `
  --output ".\data\generated\workspace-preview.json" `
  --vertex-budget 150000
```

The source CSV and generated preview are private local artifacts and must not
be committed. The frontend README documents the guarded server-only endpoint.

The import transaction commits before algorithm execution. This preserves the
raw observations when an algorithm fails and allows a later version to retry
without changing source points. Identical successful imports are reused rather
than duplicated.

## Persisting an already processed trace

When observations are already present in `app.location_points`, the lower-level
writer can persist a prepared trace directly:

```python
import os
from uuid import UUID

import psycopg

from routefrom_pipeline.database import persist_processed_trace

with psycopg.connect(os.environ["DATABASE_URL_DIRECT"]) as connection:
    result = persist_processed_trace(
        connection,
        dataset_id=UUID(dataset_id),
        dataset_import_id=UUID(dataset_import_id),
        input_sha256=source_sha256,
        trace=processed_trace,
        parameters=processing_parameters,
        code_revision=git_revision,
    )
```

This writer creates a candidate run, writes all derived stages, marks them
successful, and calls `app.activate_processing_run` inside one transaction. A
failed statement or deferred integrity constraint rolls the entire candidate
back, leaving the previous active run untouched.

Use `DATABASE_URL_DIRECT` for this long transaction. The future Next.js query
API uses pooled `DATABASE_URL`. Neither connection string belongs in browser
code or a `NEXT_PUBLIC_` variable. The library worker now exercises this writer
against a real isolated PostGIS database, including activation, overlap,
conflict decisions, failures, withdrawal and lease/revision fencing. Full-size
private CSV persistence/performance and the website map read path still need
their own end-to-end verification.

## Website library queue

The Next.js library API stores immutable CSVs privately and queues durable jobs
in `app.library_jobs`. Run the worker as a separate long-lived process, not
inside an HTTP request. Both services must use the same `FOOTPRINT_DATA_DIR`.
The website uses pooled `DATABASE_URL`; this worker uses `DATABASE_URL_DIRECT`.

With environment variables already injected by the host:

```powershell
python -m routefrom_pipeline.library_worker --data-dir "C:\private\routefrom"
```

For local development, the repository's Node runtime can inject the existing
gitignored app environment into Python without printing credentials. Run from
`processor/`:

```powershell
node --env-file=../app/.env.local --input-type=module -e 'import { spawn } from "node:child_process"; const child = spawn("../.venv/Scripts/python.exe", ["-m", "routefrom_pipeline.library_worker"], {stdio: "inherit"}); child.on("exit", code => process.exit(code ?? 1));'
```

`--once` handles at most one eligible job; `--library-id <uuid>` limits the queue
to a single library. Jobs for the same library serialize. A 15-minute renewable
lease fences stale workers. Source attachment and publication also lock the
library; a withdrawn/cancelled importer cannot re-attach it in the background.
Only a complete result for the current library revision replaces the active
snapshot. Failures and unresolved timestamp conflicts preserve the old map.

Validated conflict choices remain in published snapshot metadata even when
withdrawal makes a conflict temporarily disappear, so restoration can reuse a
still-valid choice. Only current conflicts and eligible candidates are passed
to the processing algorithm. Original files and provenance are never deleted
by withdrawal. An empty library publishes an explicit empty snapshot.

HTTP integration verification runs a production Next.js test server on port
3100 and the real worker using only synthetic CSVs and an isolated database:

```powershell
# Build app first. From app/, with ROUTEFROM_RUN_DATABASE_TESTS=true injected:
node --env-file=.env.local --test scripts/library-http-integration.test.mjs
```

This test refuses a database name outside `routefrom_integration*`. It cleans
only its synthetic users and temporary private directory. The production
database and the user's private footprint CSV are not test fixtures.
