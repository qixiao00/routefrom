from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, Sequence

EARTH_RADIUS_METERS = 6_371_008.8


class LocatedObservation(Protocol):
    recorded_at: datetime
    wgs_latitude: float
    wgs_longitude: float
    horizontal_accuracy_meters: float | None
    recorded_speed_mps: float | None
    course_degrees: float | None


@dataclass(frozen=True, slots=True)
class PointFeatures:
    point_index: int
    dt_prev_seconds: float | None
    dt_next_seconds: float | None
    distance_prev_meters: float | None
    distance_next_meters: float | None
    bypass_distance_meters: float | None
    speed_prev_mps: float | None
    speed_next_mps: float | None
    bypass_speed_mps: float | None
    triangle_ratio: float | None
    return_ratio: float | None
    turn_degrees: float | None
    recorded_speed_residual_mps: float | None
    local_interval_median_seconds: float | None
    local_interval_mad_seconds: float | None
    horizontal_accuracy_meters: float | None


def haversine_meters(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    latitude_a_rad = math.radians(latitude_a)
    latitude_b_rad = math.radians(latitude_b)
    delta_latitude = latitude_b_rad - latitude_a_rad
    delta_longitude = math.radians(longitude_b - longitude_a)
    haversine = (
        math.sin(delta_latitude / 2) ** 2
        + math.cos(latitude_a_rad)
        * math.cos(latitude_b_rad)
        * math.sin(delta_longitude / 2) ** 2
    )
    return 2 * EARTH_RADIUS_METERS * math.asin(min(1.0, math.sqrt(haversine)))


def bearing_degrees(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    latitude_a_rad = math.radians(latitude_a)
    latitude_b_rad = math.radians(latitude_b)
    delta_longitude = math.radians(longitude_b - longitude_a)
    y = math.sin(delta_longitude) * math.cos(latitude_b_rad)
    x = (
        math.cos(latitude_a_rad) * math.sin(latitude_b_rad)
        - math.sin(latitude_a_rad)
        * math.cos(latitude_b_rad)
        * math.cos(delta_longitude)
    )
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def angular_difference_degrees(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def _elapsed_seconds(a: LocatedObservation, b: LocatedObservation) -> float:
    return (b.recorded_at - a.recorded_at).total_seconds()


def _speed(distance_meters: float, elapsed_seconds: float) -> float | None:
    if elapsed_seconds <= 0:
        return None
    return distance_meters / elapsed_seconds


def _median_and_mad(values: Sequence[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    median = statistics.median(values)
    mad = statistics.median(abs(value - median) for value in values)
    return median, mad


def build_point_features(
    points: Sequence[LocatedObservation],
    *,
    interval_window: int = 6,
) -> list[PointFeatures]:
    if interval_window < 1:
        raise ValueError("interval_window must be positive")

    count = len(points)
    intervals = [
        _elapsed_seconds(points[index], points[index + 1])
        for index in range(max(0, count - 1))
    ]
    features: list[PointFeatures] = []

    for index, point in enumerate(points):
        previous = points[index - 1] if index > 0 else None
        following = points[index + 1] if index + 1 < count else None

        dt_prev = _elapsed_seconds(previous, point) if previous is not None else None
        dt_next = _elapsed_seconds(point, following) if following is not None else None
        distance_prev = (
            haversine_meters(
                previous.wgs_latitude,
                previous.wgs_longitude,
                point.wgs_latitude,
                point.wgs_longitude,
            )
            if previous is not None
            else None
        )
        distance_next = (
            haversine_meters(
                point.wgs_latitude,
                point.wgs_longitude,
                following.wgs_latitude,
                following.wgs_longitude,
            )
            if following is not None
            else None
        )

        bypass_distance: float | None = None
        bypass_speed: float | None = None
        triangle_ratio: float | None = None
        return_ratio: float | None = None
        turn_degrees: float | None = None
        if previous is not None and following is not None:
            bypass_distance = haversine_meters(
                previous.wgs_latitude,
                previous.wgs_longitude,
                following.wgs_latitude,
                following.wgs_longitude,
            )
            bypass_elapsed = _elapsed_seconds(previous, following)
            bypass_speed = _speed(bypass_distance, bypass_elapsed)
            adjacent_distance = (distance_prev or 0) + (distance_next or 0)
            triangle_ratio = adjacent_distance / max(bypass_distance, 1.0)
            return_ratio = bypass_distance / max(min(distance_prev or 0, distance_next or 0), 1.0)
            incoming_bearing = bearing_degrees(
                previous.wgs_latitude,
                previous.wgs_longitude,
                point.wgs_latitude,
                point.wgs_longitude,
            )
            outgoing_bearing = bearing_degrees(
                point.wgs_latitude,
                point.wgs_longitude,
                following.wgs_latitude,
                following.wgs_longitude,
            )
            turn_degrees = angular_difference_degrees(incoming_bearing, outgoing_bearing)

        speed_prev = (
            _speed(distance_prev, dt_prev)
            if distance_prev is not None and dt_prev is not None
            else None
        )
        speed_next = (
            _speed(distance_next, dt_next)
            if distance_next is not None and dt_next is not None
            else None
        )
        calculated_speeds = [speed for speed in (speed_prev, speed_next) if speed is not None]
        recorded_speed_residual = (
            max(calculated_speeds) - point.recorded_speed_mps
            if calculated_speeds and point.recorded_speed_mps is not None
            else None
        )

        interval_start = max(0, index - interval_window)
        interval_end = min(len(intervals), index + interval_window)
        local_intervals = [
            interval
            for interval in intervals[interval_start:interval_end]
            if 0 < interval <= 6 * 60 * 60
        ]
        interval_median, interval_mad = _median_and_mad(local_intervals)

        features.append(
            PointFeatures(
                point_index=index,
                dt_prev_seconds=dt_prev,
                dt_next_seconds=dt_next,
                distance_prev_meters=distance_prev,
                distance_next_meters=distance_next,
                bypass_distance_meters=bypass_distance,
                speed_prev_mps=speed_prev,
                speed_next_mps=speed_next,
                bypass_speed_mps=bypass_speed,
                triangle_ratio=triangle_ratio,
                return_ratio=return_ratio,
                turn_degrees=turn_degrees,
                recorded_speed_residual_mps=recorded_speed_residual,
                local_interval_median_seconds=interval_median,
                local_interval_mad_seconds=interval_mad,
                horizontal_accuracy_meters=point.horizontal_accuracy_meters,
            )
        )

    return features
