import type { FeatureCollection, Point } from "geojson";
import type { PreviewPlace } from "./workspace-data.ts";

/** Screen-overlap groups, not a permanent change to inferred place identities. */
export function buildPlaceGeoJSON(
  places: readonly PreviewPlace[], zoom: number, selectedId?: string,
): FeatureCollection<Point> {
  const groups: PreviewPlace[][] = [];
  const distance = (a: PreviewPlace, b: PreviewPlace) => {
    const lat = (a.position[1] + b.position[1]) / 2 * Math.PI / 180;
    const dx = ((a.position[0] - b.position[0] + 540) % 360 - 180) * Math.cos(lat);
    return Math.hypot(dx, a.position[1] - b.position[1]) * 111320;
  };
  // Stable order and complete-link membership prevent chains of nearby places
  // from swallowing a whole neighborhood. Never merge farther than 150 m.
  for (const place of [...places].sort((a, b) => b.visitCount - a.visitCount || a.id.localeCompare(b.id))) {
    const radius = Math.min(150, 24 * 156543.03 * Math.cos(place.position[1] * Math.PI / 180) / 2 ** zoom);
    const group = groups.find(g => g.every(p => distance(p, place) <= radius));
    if (group) group.push(place); else groups.push([place]);
  }
  return { type: "FeatureCollection", features: groups.map(group => {
    const first = group[0], visits = group.reduce((n, p) => n + p.visitCount, 0);
    const weight = group.reduce((n, p) => n + Math.max(1, p.visitCount), 0);
    const latitude = group.reduce((n, p) => n + p.position[1] * Math.max(1, p.visitCount), 0) / weight;
    const offset = group.reduce((n, p) => n + ((p.position[0] - first.position[0] + 540) % 360 - 180) * Math.max(1, p.visitCount), 0) / weight;
    return {
      type: "Feature", properties: {
        id: first.id, kind: "place", memberIds: JSON.stringify(group.map(p => p.id)),
        placeCount: group.length, visitCount: visits,
        name: group.length > 1 ? `${group.length} 个地点 · ${visits} 次访问记录` : first.name,
        frequency: Math.max(...group.map(p => p.frequentProbability)),
        selected: group.some(p => p.id === selectedId),
      },
      geometry: { type: "Point", coordinates: [((first.position[0] + offset + 540) % 360) - 180, latitude] },
    };
  }) };
}
