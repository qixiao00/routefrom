"""Bounded ZIP streaming, integrity verification and portable observation evidence.

No extraction, pickle, SQL, auth/session material or derived geometry is accepted.
The database restore writer must still enforce destination account ownership.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import struct
import zipfile
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from routefrom_pipeline.backup_manifest import (
    DEFAULT_BACKUP_LIMITS,
    BackupError,
    BackupLimits,
    identifier,
    validate_manifest,
)
from routefrom_pipeline.ingest.library import observation_fingerprint
from routefrom_pipeline.ingest.linggan import iter_linggan_stream
from routefrom_pipeline.model import NormalizedLocationPoint

CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True)
class VerifiedBackup:
    manifest: dict
    references: dict[tuple[str, str], NormalizedLocationPoint]
    point_counts: dict[str, int]
    archive_sha256: str


def _json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise BackupError("duplicate JSON key")
        value[key] = item
    return value


def _reject_constant(_):
    raise BackupError("non-finite JSON number")


def _zip_directory_guard(handle, limits):
    """Bound the directory BEFORE ZipFile materializes its member objects.

    The v1 writer uses a single ordinary ZIP disk, no comments or ZIP64.
    Every permitted size/count is below the ZIP64 boundary.
    """
    size = os.fstat(handle.fileno()).st_size
    if not 22 <= size <= limits.archive_bytes:
        raise BackupError("backup archive exceeds limits")
    handle.seek(-22, 2)
    signature, disk, directory_disk, count_disk, count, length, offset, comment = struct.unpack(
        "<4s4H2LH", handle.read(22)
    )
    if (
        signature != b"PK\x05\x06"
        or disk
        or directory_disk
        or comment
        or count != count_disk
        or not 1 <= count <= limits.files + 1
    ):
        raise BackupError("unsupported ZIP directory")
    if length > 256 * 1024 or offset + length != size - 22:
        raise BackupError("invalid or excessive ZIP directory")
    handle.seek(0)
    if handle.read(4) != b"PK\x03\x04":
        raise BackupError("unexpected ZIP prefix")
    handle.seek(0)


def _check_member(info):
    kind = stat.S_IFMT(info.external_attr >> 16)
    if info.orig_filename != info.filename or info.is_dir() or kind not in (0, stat.S_IFREG):
        raise BackupError("ZIP entries must be ordinary files")
    if info.flag_bits & 1 or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise BackupError("encrypted or unsupported ZIP entry")
    if info.extra:
        raise BackupError("extended ZIP entries are not supported by backup v1")


def verify_backup(
    path: str | Path, *, limits: BackupLimits = DEFAULT_BACKUP_LIMITS
) -> VerifiedBackup:
    try:
        with Path(path).open("rb") as handle:
            _zip_directory_guard(handle, limits)
            archive_digest = hashlib.file_digest(handle, "sha256").hexdigest()
            handle.seek(0)
            with zipfile.ZipFile(handle) as archive:
                entries = archive.infolist()
                if len(entries) > limits.files + 1 or len(
                    {entry.filename for entry in entries}
                ) != len(entries):
                    raise BackupError("duplicate or excessive archive members")
                for entry in entries:
                    _check_member(entry)
                metadata = archive.getinfo("manifest.json")
                if metadata.file_size > limits.manifest_bytes:
                    raise BackupError("backup manifest exceeds limits")
                with archive.open(metadata) as stream:
                    data = stream.read(limits.manifest_bytes + 1)
                if len(data) > limits.manifest_bytes:
                    raise BackupError("backup manifest exceeds limits")
                manifest = json.loads(
                    data.decode("utf-8"),
                    object_pairs_hook=_json_object,
                    parse_constant=_reject_constant,
                )
                wanted = validate_manifest(manifest, limits)
                if {entry.filename for entry in entries} != {
                    "manifest.json",
                    *(file["member"] for file in manifest["files"]),
                }:
                    raise BackupError("unexpected or missing archive member")
                references = {}
                counts = {}
                total_points = 0
                for file in manifest["files"]:
                    entry = archive.getinfo(file["member"])
                    if entry.file_size != file["size"]:
                        raise BackupError("original size does not match manifest")
                    digest = hashlib.sha256()
                    length = 0
                    with archive.open(entry) as stream:
                        while chunk := stream.read(CHUNK_BYTES):
                            length += len(chunk)
                            if length > file["size"]:
                                raise BackupError("original exceeds declared size")
                            digest.update(chunk)
                    if length != file["size"] or digest.hexdigest() != file["sha256"]:
                        raise BackupError("original digest does not match manifest")
                    if file["status"] == "unprocessed":
                        continue  # Preserve failed/pending originals, never trust them as evidence.
                    count = 0
                    with (
                        archive.open(entry) as stream,
                        io.TextIOWrapper(stream, encoding="utf-8-sig", newline="") as text,
                    ):
                        for point in iter_linggan_stream(text):
                            count += 1
                            total_points += 1
                            if total_points > limits.points:
                                raise BackupError("original observations exceed limits")
                            key = (
                                file["sourceId"],
                                observation_fingerprint(point, file["sourceId"]),
                            )
                            if key in wanted:
                                references[key] = point
                    if not count:
                        raise BackupError("registered original has no observations")
                    counts[file["id"]] = count
                if set(references) != wanted:
                    raise BackupError("note or decision has no exact original evidence")
                for decision in manifest["decisions"]:
                    if (
                        references[
                            (decision["sourceId"], decision["fingerprint"])
                        ].geo_time_epoch_ms
                        != decision["epochMs"]
                    ):
                        raise BackupError("decision timestamp does not match its evidence")
                handle.seek(0)
                if hashlib.file_digest(handle, "sha256").hexdigest() != archive_digest:
                    raise BackupError("backup changed during verification")
                return VerifiedBackup(manifest, references, counts, archive_digest)
    except BackupError:
        raise
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        UnicodeError,
        zipfile.BadZipFile,
        RuntimeError,
        RecursionError,
        EOFError,
        zlib.error,
    ) as error:
        raise BackupError("backup is unreadable or malformed") from error


def _member_info(name):
    info = zipfile.ZipInfo(name)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    return info


def write_backup(
    path: str | Path,
    manifest: dict,
    originals: Mapping[str, Path],
    *,
    limits: BackupLimits = DEFAULT_BACKUP_LIMITS,
) -> VerifiedBackup:
    """Create exclusively; reject changed/missing originals and remove only our partial output."""
    validate_manifest(manifest, limits)
    data = json.dumps(manifest, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode(
        "utf-8"
    )
    if len(data) > limits.manifest_bytes or set(originals) != {
        file["id"] for file in manifest["files"]
    }:
        raise BackupError("manifest or original file mapping is incomplete")
    target = Path(path)
    descriptor = os.open(target, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with (
            os.fdopen(descriptor, "w+b") as handle,
            zipfile.ZipFile(handle, "w", allowZip64=False) as archive,
        ):
            archive.writestr(_member_info("manifest.json"), data)
            for file in manifest["files"]:
                digest = hashlib.sha256()
                length = 0
                with (
                    Path(originals[file["id"]]).open("rb") as stream,
                    archive.open(_member_info(file["member"]), "w") as entry,
                ):
                    while chunk := stream.read(CHUNK_BYTES):
                        length += len(chunk)
                        if length > file["size"]:
                            raise BackupError("original changed while creating backup")
                        digest.update(chunk)
                        entry.write(chunk)
                    if length != file["size"] or digest.hexdigest() != file["sha256"]:
                        raise BackupError("original changed while creating backup")
        return verify_backup(target, limits=limits)
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def remap_backup_evidence(backup: VerifiedBackup, target_sources: Mapping[str, str]):
    """Produce portable decisions/anchors; caller must resolve these within the destination owner."""
    expected = {source["id"] for source in backup.manifest["sources"]}
    if set(target_sources) != expected or len(set(target_sources.values())) != len(expected):
        raise BackupError("destination source mapping must be complete and one-to-one")
    for target in target_sources.values():
        identifier(target)
    mapped = {
        key: {
            "sourceId": target_sources[key[0]],
            "fingerprint": observation_fingerprint(point, target_sources[key[0]]),
        }
        for key, point in backup.references.items()
    }
    decisions = {
        decision["epochMs"]: mapped[(decision["sourceId"], decision["fingerprint"])]["fingerprint"]
        for decision in backup.manifest["decisions"]
    }
    notes = [
        {
            **{key: value for key, value in note.items() if key != "id"},
            "originId": note["id"],
            "anchors": [
                dict(mapped[(anchor["sourceId"], anchor["fingerprint"])])
                for anchor in note["anchors"]
            ],
        }
        for note in backup.manifest["notes"]
    ]
    return {"decisions": decisions, "notes": notes}
