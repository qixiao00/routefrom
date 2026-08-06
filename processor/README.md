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
code or a `NEXT_PUBLIC_` variable. PostgreSQL/PostGIS integration still needs
to be exercised against a real migrated database before this path is considered
production-verified.
