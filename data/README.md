# Footprint data workspace

This directory contains private source data and local processing artifacts.
Its contents are intentionally excluded from Git.

- `raw/`: immutable source uploads. Never edit files in place.
- `working/`: temporary files produced during an import.
- `generated/`: reusable map caches, simplified tracks, and summaries.
- `reports/`: import validation and data-quality reports.

Every import should use a unique dataset ID. Derived data must be reproducible
from the original file and versioned processing code.

