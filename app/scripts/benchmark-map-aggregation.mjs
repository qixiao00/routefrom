import { readFile } from "node:fs/promises";
import { aggregateOrdinaryRoutes } from "../src/lib/map-route-aggregation.ts";
import { buildPlaceGeoJSON } from "../src/lib/map-place-data.ts";
import { buildViewportSelection, queryViewportSelection } from "../src/lib/workspace-viewport.ts";

const preview = JSON.parse(await readFile(process.argv[2] ?? "../data/generated/workspace-preview.json", "utf8"));
for (const [name, ranges] of [["recent", preview.suggestedRanges], ["all-time", [{ start: preview.dataset.startedAt, end: preview.dataset.endedAt }]]]) {
  const selection = buildViewportSelection(preview.paths, ranges, preview.modeLegs);
  for (const zoom of [9, 11, 14]) {
    const query = queryViewportSelection(selection, [117.3, 23.9, 119, 25.2], zoom);
    const start = performance.now();
    const result = aggregateOrdinaryRoutes(query.paths, zoom);
    console.log(JSON.stringify({ name, zoom, milliseconds: Math.round(performance.now() - start),
      inputEdges: query.paths.reduce((n, p) => n + p.vertices.length - 1, 0),
      displayVertices: result.features.reduce((n, f) => n + f.geometry.coordinates.reduce((m, line) => m + line.length, 0), 0),
      bytes: JSON.stringify(result).length,
      repeatedParts: result.features.filter(f => f.properties.traversalCount > 1).reduce((n, f) => n + f.geometry.coordinates.length, 0),
    }));
  }
}
console.log(JSON.stringify({ places: preview.places.length, groupedAtZoom11: buildPlaceGeoJSON(preview.places, 11).features.length }));
