from __future__ import annotations

import json
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .matching import (
    MapMatchConfig,
    MapMatchRequest,
    MatchedObservation,
    MatcherResponse,
)


def decode_polyline6(encoded: str) -> tuple[tuple[float, float], ...]:
    index = latitude = longitude = 0
    coordinates: list[tuple[float, float]] = []
    while index < len(encoded):
        changes: list[int] = []
        for _ in range(2):
            result = shift = 0
            while True:
                if index >= len(encoded):
                    raise ValueError("truncated encoded polyline")
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            changes.append(~(result >> 1) if result & 1 else result >> 1)
        latitude += changes[0]
        longitude += changes[1]
        coordinates.append((latitude / 1_000_000, longitude / 1_000_000))
    return tuple(coordinates)


class ValhallaMapMatcher:
    name = "valhalla_meili"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8002",
        *,
        version: str = "unknown",
        timeout_seconds: float = 30.0,
        allow_remote: bool = False,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("Valhalla base URL must be an HTTP(S) URL")
        if not allow_remote and parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("remote Valhalla is disabled to protect private trajectory data")
        self.base_url = base_url.rstrip("/")
        self.version = version
        self.timeout_seconds = timeout_seconds

    def _post(self, payload: dict[str, object]) -> dict[str, object]:
        request = Request(
            f"{self.base_url}/trace_attributes",
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
        if not isinstance(body, dict):
            raise ValueError("Valhalla returned a non-object response")
        return body

    def match(self, request: MapMatchRequest, config: MapMatchConfig) -> MatcherResponse:
        shape = [
            {
                "lat": latitude,
                "lon": longitude,
                "time": int(recorded_at.timestamp()),
            }
            for (latitude, longitude), recorded_at in zip(
                request.coordinates, request.recorded_at
            )
        ]
        response = self._post(
            {
                "shape": shape,
                "costing": request.costing,
                "shape_match": "map_snap",
                "trace_options": {
                    "search_radius": config.search_radius_meters,
                    "gps_accuracy": statistics_median(request.accuracy_meters),
                },
                "filters": {
                    "action": "include",
                    "attributes": [
                        "shape",
                        "osm_changeset",
                        "edge.id",
                        "edge.way_id",
                        "edge.begin_shape_index",
                        "edge.end_shape_index",
                        "edge.travel_mode",
                        "edge.use",
                        "matched.point",
                        "matched.type",
                        "matched.edge_index",
                        "matched.begin_route_discontinuity",
                        "matched.end_route_discontinuity",
                        "matched.distance_along_edge",
                        "matched.distance_from_trace_point",
                    ],
                },
            }
        )
        encoded_shape = response.get("shape")
        raw_edges = response.get("edges")
        raw_points = response.get("matched_points")
        if not isinstance(encoded_shape, str) or not isinstance(raw_edges, list):
            raise ValueError("Valhalla response is missing shape or edges")
        if not isinstance(raw_points, list) or len(raw_points) != len(request.point_indices):
            raise ValueError("Valhalla matched_points must correspond one-to-one with input")
        edges = [edge for edge in raw_edges if isinstance(edge, dict)]
        observations: list[MatchedObservation] = []
        for point_index, raw in zip(request.point_indices, raw_points):
            if not isinstance(raw, dict):
                raise ValueError("Valhalla returned an invalid matched point")
            edge_index = raw.get("edge_index")
            edge = (
                edges[edge_index]
                if isinstance(edge_index, int) and 0 <= edge_index < len(edges)
                else None
            )
            route_position = None
            if edge is not None and isinstance(raw.get("distance_along_edge"), (int, float)):
                begin = edge.get("begin_shape_index")
                end = edge.get("end_shape_index")
                if isinstance(begin, int) and isinstance(end, int):
                    fraction = min(1.0, max(0.0, float(raw["distance_along_edge"])))
                    route_position = begin + (end - begin) * fraction
            observations.append(
                MatchedObservation(
                    point_index=point_index,
                    latitude=float(raw["lat"]) if isinstance(raw.get("lat"), (int, float)) else None,
                    longitude=float(raw["lon"]) if isinstance(raw.get("lon"), (int, float)) else None,
                    match_type=str(raw.get("type", "unmatched")),
                    edge_id=str(edge["id"]) if edge is not None and "id" in edge else None,
                    way_id=int(edge["way_id"]) if edge is not None and isinstance(edge.get("way_id"), int) else None,
                    distance_from_trace_point_meters=(
                        float(raw["distance_from_trace_point"])
                        if isinstance(raw.get("distance_from_trace_point"), (int, float))
                        else None
                    ),
                    route_position=route_position,
                    begin_route_discontinuity=bool(raw.get("begin_route_discontinuity", False)),
                    end_route_discontinuity=bool(raw.get("end_route_discontinuity", False)),
                )
            )
        return MatcherResponse(
            path=decode_polyline6(encoded_shape),
            observations=tuple(observations),
            edge_ids=tuple(str(edge["id"]) for edge in edges if "id" in edge),
            way_ids=tuple(int(edge["way_id"]) for edge in edges if isinstance(edge.get("way_id"), int)),
            provider_metadata={
                "osm_changeset": response.get("osm_changeset"),
                "warnings": response.get("warnings", []),
            },
        )


def statistics_median(values: tuple[float, ...]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2
