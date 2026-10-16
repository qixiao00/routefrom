"""Private immutable CSV paths shared by the worker and backup exporter."""

import re
from pathlib import Path


def private_csv_path(root: Path, key: str) -> Path:
    if not re.fullmatch(r"(?:raw|generated)/[0-9a-f-]{36}/[0-9a-f-]{36}\.csv", key):
        raise ValueError("invalid private source key")
    canonical_root = root.resolve()
    expected = canonical_root.joinpath(*key.split("/"))
    resolved = expected.resolve()
    if resolved != expected:
        raise ValueError("private source cannot redirect to another asset")
    return resolved
