import { readFile } from "node:fs/promises";
import { performance } from "node:perf_hooks";
import path from "node:path";

import { buildViewportSelection, queryViewportSelection } from "../src/lib/workspace-viewport.ts";

const previewPath = process.argv[2] ?? path.resolve("..", "data", "generated", "workspace-preview.json");
const source = await readFile(previewPath, "utf8");
const preview = JSON.parse(source);
const bootstrap = JSON.stringify({
  ...preview,
  paths: [],
  gaps: [],
  stays: [],
  trips: [],
  modeLegs: [],
});
const last = preview.paths.at(-1)?.vertices.at(-1);
if (!last) throw new Error("preview has no trajectory vertices");

const started = performance.now();
const selection = buildViewportSelection(preview.paths, preview.suggestedRanges, preview.modeLegs);
const selectedAt = performance.now();
const bounds = [last[1] - 0.4, last[2] - 0.3, last[1] + 0.4, last[2] + 0.3];
const nearby = queryViewportSelection(selection, bounds, 11);
const nearbyAt = performance.now();
const overview = queryViewportSelection(selection, [-180, -90, 180, 90], 4);
const overviewAt = performance.now();
const allTimeSelection = buildViewportSelection(preview.paths, [{
  start: preview.dataset.startedAt,
  end: preview.dataset.endedAt,
}], preview.modeLegs);
const allTimeAt = performance.now();
const allTimeOverview = queryViewportSelection(allTimeSelection, [-180, -90, 180, 90], 4);
const allTimeQueryAt = performance.now();
const allTimeNearby = queryViewportSelection(allTimeSelection, bounds, 14);
const allTimeNearbyAt = performance.now();

console.log(JSON.stringify({
  sourceBytes: Buffer.byteLength(source),
  bootstrapBytes: Buffer.byteLength(bootstrap),
  selectedPathCount: selection.paths.length,
  selectionMs: Math.round(selectedAt - started),
  nearby: {
    bytes: Buffer.byteLength(JSON.stringify(nearby)),
    vertices: nearby.visibleVertexCount,
    paths: nearby.paths.length,
    queryMs: Math.round(nearbyAt - selectedAt),
  },
  overview: {
    bytes: Buffer.byteLength(JSON.stringify(overview)),
    vertices: overview.visibleVertexCount,
    paths: overview.paths.length,
    queryMs: Math.round(overviewAt - nearbyAt),
  },
  allTimeOverview: {
    bytes: Buffer.byteLength(JSON.stringify(allTimeOverview)),
    vertices: allTimeOverview.visibleVertexCount,
    paths: allTimeOverview.paths.length,
    selectionMs: Math.round(allTimeAt - overviewAt),
    queryMs: Math.round(allTimeQueryAt - allTimeAt),
  },
  allTimeNearby: {
    bytes: Buffer.byteLength(JSON.stringify(allTimeNearby)),
    vertices: allTimeNearby.visibleVertexCount,
    paths: allTimeNearby.paths.length,
    queryMs: Math.round(allTimeNearbyAt - allTimeQueryAt),
  },
}, null, 2));
