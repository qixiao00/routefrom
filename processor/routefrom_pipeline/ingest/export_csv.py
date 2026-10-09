"""Lossless Linggan-format CSV projection for exports and processing snapshots."""
from __future__ import annotations

import csv
from collections.abc import Iterable
from typing import TextIO

from routefrom_pipeline.ingest.linggan import EXPECTED_COLUMNS
from routefrom_pipeline.model import NormalizedLocationPoint


def write_linggan_csv(handle: TextIO, points: Iterable[NormalizedLocationPoint]) -> int:
    writer = csv.writer(handle, lineterminator="\n")
    writer.writerow(EXPECTED_COLUMNS)
    count = 0
    for point in points:
        writer.writerow((
            point.geo_time_epoch_ms, point.source_latitude, point.source_longitude,
            point.wgs_latitude, point.wgs_longitude, point.source_altitude,
            point.source_course, point.source_horizontal_accuracy,
            point.source_vertical_accuracy, point.source_speed, point.day_time_epoch_ms,
            point.network_type, point.network_name, point.location_type,
        ))
        count += 1
    return count
