# Workspace trajectory query API

`POST /api/workspace/query` reads the preferred trajectory representation from
the active (or explicitly requested) processing run. It accepts up to 64
arbitrary UTC-offset time ranges. Ranges may cross days, months, and years; they
are sorted and unionized only when they overlap or touch. Discontinuous ranges
always produce separate paths.

```json
{
  "datasetId": "ab96de99-f2d3-402b-ad2b-c756e05d4d62",
  "processingRunId": "active",
  "timeSelection": {
    "timezone": "Asia/Shanghai",
    "ranges": [
      {
        "start": "2026-07-15T10:00:15+08:00",
        "end": "2026-07-15T10:03:45+08:00"
      },
      {
        "start": "2027-02-03T17:12:00+08:00",
        "end": "2027-02-03T17:20:00+08:00"
      }
    ]
  },
  "trajectoryDetail": {
    "pixelToleranceMeters": 5,
    "vertexBudget": 50000
  }
}
```

The database query first selects calendar-independent chunks that overlap each
range. The server then clips crossing edges exactly at the requested boundaries
and applies the stored effective-area importance values. Semantic anchors remain
visible at every precision; a vertex budget is shared across all returned paths.

## Temporary access boundary

Authentication has not been selected yet, so the endpoint is disabled by
default. For local single-user development, copy `.env.example` to `.env.local`
and set all of:

```text
DATABASE_URL=<pooled Neon connection string>
ROUTEFROM_ENABLE_LOCAL_DATA_API=true
ROUTEFROM_ALLOWED_DATASET_ID=<one dataset UUID>
```

This is a deliberate temporary guard, not production authentication. A request
for any other dataset returns `404`, and missing configuration returns `503`.
The endpoint always uses `Cache-Control: private, no-store`. Never put a database
URL in a `NEXT_PUBLIC_` variable.
