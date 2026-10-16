"""Strict, account-independent RouteFrom backup v1 metadata contract."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from routefrom_pipeline.ingest.library import IDENTITY_VERSION

FORMAT = "routefrom-library-backup"
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class BackupError(ValueError):
    """Invalid, unsupported, incomplete or resource-exceeding archive."""


@dataclass(frozen=True)
class BackupLimits:
    archive_bytes: int = 1024**3 + 34 * 1024**2  # raw capacity + manifest + ZIP overhead
    total_bytes: int = 1024**3
    file_bytes: int = 80 * 1024**2
    manifest_bytes: int = 32 * 1024**2
    files: int = 256
    sources: int = 128
    references: int = 100_000
    points: int = 5_000_000


DEFAULT_BACKUP_LIMITS = BackupLimits()


def record(value, keys: str):
    if not isinstance(value, dict) or set(value) != set(keys.split()):
        raise BackupError("unexpected or missing metadata fields")
    return value


def text(value, maximum: int, *, nonempty=True, utf16=False):
    if not isinstance(value, str) or "\0" in value:
        raise BackupError("invalid metadata text")
    try:
        value.encode("utf-8")
        length = len(value.encode("utf-16-le")) // 2 if utf16 else len(value)
    except UnicodeError as error:
        raise BackupError("invalid metadata Unicode") from error
    if length > maximum or (nonempty and not value.strip()):
        raise BackupError("metadata text exceeds limits or is empty")
    return value


def identifier(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError) as error:
        raise BackupError("invalid metadata UUID") from error
    return value


def integer(value, minimum=0, maximum=2**63 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise BackupError("invalid metadata integer")
    return value


def instant(value):
    try:
        text(value, 64)
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            raise ValueError()
        return parsed
    except (ValueError, TypeError) as error:
        raise BackupError("timestamp must have a UTC offset") from error


def number(value, minimum, maximum):
    if (
        type(value) not in (int, float)
        or not math.isfinite(value)
        or not minimum <= value <= maximum
    ):
        raise BackupError("invalid metadata number")


def items(value, maximum):
    if not isinstance(value, list) or len(value) > maximum:
        raise BackupError("metadata collection exceeds limits")
    return value


def unique_ids(values):
    result = [identifier(value["id"]) for value in values]
    if len(set(result)) != len(result):
        raise BackupError("duplicate metadata identity")
    return set(result)


def reference(value, sources):
    record(value, "sourceId fingerprint")
    if (
        identifier(value["sourceId"]) not in sources
        or not isinstance(value["fingerprint"], str)
        or not SHA256.fullmatch(value["fingerprint"])
    ):
        raise BackupError("invalid observation reference")
    return value["sourceId"], value["fingerprint"]


def validate_map_config(value):
    """Mirror the implemented map-view v1 wire contract, not arbitrary Zustand state."""
    record(value, "schemaVersion map ranges visibleLayers panels")
    if type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1:
        raise BackupError("unsupported map configuration")
    view = record(value["map"], "projection style terrain buildings camera")
    if (
        view["projection"] != "globe"
        or view["style"] != "openfreemap-dark-v1"
        or view["terrain"] is not False
        or view["buildings"] is not False
    ):
        raise BackupError("unsupported map configuration")
    camera = record(view["camera"], "longitude latitude zoom pitch bearing")
    for key, bounds in {
        "longitude": (-180, 180),
        "latitude": (-90, 90),
        "zoom": (0, 24),
        "pitch": (0, 85),
        "bearing": (-360, 360),
    }.items():
        number(camera[key], *bounds)
    for field, keys in (
        ("visibleLayers", "track sparse highSpeed stays gaps places"),
        ("panels", "layers inspector"),
    ):
        flags = record(value[field], keys)
        if any(type(flag) is not bool for flag in flags.values()):
            raise BackupError("map visibility flags must be booleans")
    for span in items(value["ranges"], 64):
        record(span, "start end")
        if instant(span["start"]) >= instant(span["end"]):
            raise BackupError("empty map time range")


def validate_manifest(value, limits=DEFAULT_BACKUP_LIMITS):
    record(
        value,
        "format version identityVersion createdAt library sources files decisions notes workspace views",
    )
    if (
        value["format"] != FORMAT
        or type(value["version"]) is not int
        or value["version"] != 1
        or value["identityVersion"] != IDENTITY_VERSION
    ):
        raise BackupError("unsupported backup or observation identity version")
    instant(value["createdAt"])
    library = record(value["library"], "name timezone revision")
    text(library["name"], 256)
    integer(library["revision"])
    try:
        ZoneInfo(text(library["timezone"], 100))
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise BackupError("unsupported library timezone") from error
    sources = items(value["sources"], limits.sources)
    for source in sources:
        record(source, "id key name parser createdAt")
        instant(source["createdAt"])
        text(source["key"], 128)
        text(source["name"], 512)
        if source["parser"] != "linggan_footprint_csv":
            raise BackupError("unsupported source parser")
    source_ids = unique_ids(sources)
    if len({source["key"] for source in sources}) != len(sources):
        raise BackupError("duplicate source key")
    files = items(value["files"], limits.files)
    total = 0
    for file in files:
        record(
            file,
            "id sourceId status datasetStatus jobStatus name size sha256 member uploadedAt addedAt withdrawnAt",
        )
        identifier(file["id"])
        text(file["name"], 1024)
        instant(file["uploadedAt"])
        if file["datasetStatus"] not in (
            "pending",
            "validating",
            "processing",
            "ready",
            "failed",
            "archived",
        ):
            raise BackupError("invalid original processing status")
        if file["jobStatus"] not in (
            None,
            "queued",
            "running",
            "awaiting_resolution",
            "succeeded",
            "failed",
            "superseded",
        ):
            raise BackupError("invalid original job status")
        if not file["name"].lower().endswith(".csv") or file["status"] not in (
            "active",
            "withdrawn",
            "unprocessed",
        ):
            raise BackupError("invalid original CSV metadata")
        if file["sourceId"] is not None and identifier(file["sourceId"]) not in source_ids:
            raise BackupError("unknown file source")
        if file["status"] != "unprocessed" and file["sourceId"] is None:
            raise BackupError("registered file requires a source")
        if file["status"] == "unprocessed":
            if file["addedAt"] is not None or file["withdrawnAt"] is not None:
                raise BackupError("unregistered original has library membership dates")
        else:
            instant(file["addedAt"])
            if file["status"] == "withdrawn":
                instant(file["withdrawnAt"])
            elif file["withdrawnAt"] is not None:
                raise BackupError("active original has withdrawal date")
        if (
            file["member"] != f"raw/{file['id']}.csv"
            or not isinstance(file["sha256"], str)
            or not SHA256.fullmatch(file["sha256"])
        ):
            raise BackupError("invalid archive member or digest")
        total += integer(file["size"], maximum=limits.file_bytes)
    unique_ids(files)
    if total > limits.total_bytes or len({file["sha256"] for file in files}) != len(files):
        raise BackupError("original data exceeds limits or duplicates a stored file")
    wanted = set()
    epochs = set()
    for decision in items(value["decisions"], limits.references):
        record(decision, "epochMs sourceId fingerprint")
        epoch = integer(decision["epochMs"], -(2**53 - 1), 2**53 - 1)
        if epoch in epochs:
            raise BackupError("duplicate timestamp decision")
        epochs.add(epoch)
        wanted.add(
            reference({key: decision[key] for key in ("sourceId", "fingerprint")}, source_ids)
        )
    for note in items(value["notes"], 1000):
        record(note, "id label position anchors revision createdAt updatedAt")
        integer(note["revision"], 1)
        instant(note["createdAt"])
        instant(note["updatedAt"])
        text(note["label"], 80, utf16=True)
        if not isinstance(note["position"], list) or len(note["position"]) != 2:
            raise BackupError("invalid note position")
        number(note["position"][0], -180, 180)
        number(note["position"][1], -90, 90)
        anchors = [reference(anchor, source_ids) for anchor in items(note["anchors"], 12)]
        if len(set(anchors)) != len(anchors):
            raise BackupError("duplicate note anchor")
        wanted.update(anchors)
    unique_ids(value["notes"])
    if len(wanted) > limits.references:
        raise BackupError("too many referenced observations")
    if value["workspace"] is not None:
        state = record(value["workspace"], "config revision updatedAt")
        validate_map_config(state["config"])
        integer(state["revision"], 1)
        instant(state["updatedAt"])
    for view in items(value["views"], 100):
        record(view, "id title config createdAt")
        text(view["title"], 80, utf16=True)
        instant(view["createdAt"])
        validate_map_config(view["config"])
    unique_ids(value["views"])
    return wanted
