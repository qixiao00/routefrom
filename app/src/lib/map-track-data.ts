import type { FeatureCollection, MultiLineString } from "geojson";
import type { ViewportPath } from "./workspace-viewport.ts";

export function buildTrackGeoJSON(
  paths: readonly ViewportPath[],
  visible: { track: boolean; sparse: boolean; highSpeed: boolean },
): FeatureCollection<MultiLineString> {
  const groups = new Map<string, {
    rangeIndex: number;
    movementClass: ViewportPath["movementClass"];
    coordinates: number[][][];
  }>();
  for (const path of paths) {
    if (path.vertices.length < 2) continue;
    if (
      (path.movementClass === "ordinary" && !visible.track) ||
      (path.movementClass === "sparse" && !visible.sparse) ||
      (path.movementClass === "high_speed" && !visible.highSpeed)
    ) continue;
    const key = `${path.rangeIndex}:${path.movementClass}`;
    const group = groups.get(key) ?? {
      rangeIndex: path.rangeIndex,
      movementClass: path.movementClass,
      coordinates: [],
    };
    // Each input fragment remains a separate LineString, even within a group.
    group.coordinates.push(path.vertices.map((vertex) => [vertex[1], vertex[2]]));
    groups.set(key, group);
  }
  return {
    type: "FeatureCollection",
    features: [...groups.values()].map(({ rangeIndex, movementClass, coordinates }) => ({
      type: "Feature",
      properties: { paletteIndex: rangeIndex % 2, movementClass, traversalCount: 1 },
      geometry: { type: "MultiLineString", coordinates },
    })),
  };
}
