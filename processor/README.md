# RouteFrom processing worker

`routefrom_pipeline` turns immutable Linggan observations into versioned,
explainable quality, continuity, stay, trip, mode, and trajectory results.

## Persisting a processed trace

Install the database extra and connect with the direct (non-pooled) database
URL. Imported observations must already exist in `app.location_points`; the
writer matches them by import ID and source row and never updates them.

```powershell
python -m pip install -e ".[database]"
```

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

The writer creates a candidate run, writes all derived stages, marks them
successful, and calls `app.activate_processing_run` inside one transaction. A
failed statement or deferred integrity constraint rolls the entire candidate
back, leaving the previous active run untouched.

Use `DATABASE_URL_DIRECT` for this long transaction. The future Next.js query
API uses pooled `DATABASE_URL`. Neither connection string belongs in browser
code or a `NEXT_PUBLIC_` variable.
