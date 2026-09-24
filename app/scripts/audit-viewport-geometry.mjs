import { readFile } from "node:fs/promises";
import path from "node:path";

import { buildViewportSelection, queryViewportSelection } from "../src/lib/workspace-viewport.ts";

const previewPath = process.argv[2] ?? path.resolve("..", "data", "generated", "workspace-preview.json");
const preview = JSON.parse(await readFile(previewPath, "utf8"));
const selection = buildViewportSelection(preview.paths, [{
  start: preview.dataset.startedAt,
  end: preview.dataset.endedAt,
}], preview.modeLegs);

function distanceToSegmentMeters(point, start, end) {
  const radians = Math.PI / 180;
  const latitude = ((start[2] + end[2]) / 2) * radians;
  const scaleX = 6_371_008.8 * radians * Math.cos(latitude);
  const scaleY = 6_371_008.8 * radians;
  const px = (point[1] - start[1]) * scaleX;
  const py = (point[2] - start[2]) * scaleY;
  const ex = (end[1] - start[1]) * scaleX;
  const ey = (end[2] - start[2]) * scaleY;
  const lengthSquared = ex * ex + ey * ey;
  const fraction = lengthSquared === 0 ? 0 : Math.max(0, Math.min(1, (px * ex + py * ey) / lengthSquared));
  return Math.hypot(px - fraction * ex, py - fraction * ey);
}

function auditZoom(zoom) {
  const response = queryViewportSelection(selection, [-180, -90, 180, 90], zoom);
  if (response.paths.length !== selection.paths.length) {
    throw new Error("world query changed the number of independent path pieces");
  }
  let maximumDeviationMeters = 0;
  let violatingEdges = 0;
  let evaluatedEdges = 0;
  const worst = [];
  for (let index = 0; index < response.paths.length; index += 1) {
    const source = selection.paths[index].path;
    const simplified = response.paths[index];
    if (source.segmentIndex !== simplified.segmentIndex || source.movementClass !== simplified.movementClass) {
      throw new Error("world query changed path order or movement class");
    }
    let sourceIndex = 0;
    for (let keptIndex = 1; keptIndex < simplified.vertices.length; keptIndex += 1) {
      const left = simplified.vertices[keptIndex - 1];
      const right = simplified.vertices[keptIndex];
      let edgeDeviation = 0;
      while (sourceIndex < source.vertices.length && source.vertices[sourceIndex][0] !== right[0]) {
        edgeDeviation = Math.max(
          edgeDeviation,
          distanceToSegmentMeters(source.vertices[sourceIndex], left, right),
        );
        sourceIndex += 1;
      }
      if (sourceIndex >= source.vertices.length) throw new Error("simplified vertex is not a source vertex");
      sourceIndex += 1;
      evaluatedEdges += 1;
      maximumDeviationMeters = Math.max(maximumDeviationMeters, edgeDeviation);
      const tolerance = 156_543.03 / 2 ** zoom * 0.8;
      if (edgeDeviation > tolerance + 2) {
        violatingEdges += 1;
        worst.push({
          segmentIndex: source.segmentIndex,
          movementClass: source.movementClass,
          start: left[0],
          end: right[0],
          deviation: Math.round(edgeDeviation),
          tolerance: Math.round(tolerance),
        });
      }
    }
  }
  return {
    zoom,
    vertices: response.visibleVertexCount,
    paths: response.paths.length,
    evaluatedEdges,
    maximumDeviationMeters: Math.round(maximumDeviationMeters),
    violatingEdges,
    worst: worst.sort((left, right) => right.deviation / right.tolerance - left.deviation / left.tolerance).slice(0, 3),
  };
}

const sourceByClass = new Map();
for (const { path: source } of selection.paths) {
  const key = `${source.rangeIndex}:${source.segmentIndex}:${source.movementClass}`;
  const candidates = sourceByClass.get(key) ?? [];
  candidates.push(source);
  sourceByClass.set(key, candidates);
}

function auditLocalZoom(zoom, bounds) {
  const response = queryViewportSelection(selection, bounds, zoom);
  const tolerance = 156_543.03 * Math.cos(((bounds[1] + bounds[3]) / 2) * Math.PI / 180)
    / 2 ** zoom * 0.8;
  let maximumDeviationMeters = 0;
  let violatingEdges = 0;
  let evaluatedEdges = 0;
  for (const piece of response.paths) {
    const key = `${piece.rangeIndex}:${piece.segmentIndex}:${piece.movementClass}`;
    const source = sourceByClass.get(key)?.find((candidate) =>
      Date.parse(candidate.vertices[0][0]) <= Date.parse(piece.vertices[0][0]) &&
      Date.parse(candidate.vertices.at(-1)[0]) >= Date.parse(piece.vertices.at(-1)[0]) &&
      candidate.vertices.some((vertex) => vertex[0] === piece.vertices[0][0])
    );
    if (!source) throw new Error("local viewport piece has no matching source path");
    let sourceIndex = source.vertices.findIndex((vertex) => vertex[0] === piece.vertices[0][0]);
    for (let keptIndex = 1; keptIndex < piece.vertices.length; keptIndex += 1) {
      const left = piece.vertices[keptIndex - 1];
      const right = piece.vertices[keptIndex];
      let edgeDeviation = 0;
      while (sourceIndex < source.vertices.length && source.vertices[sourceIndex][0] !== right[0]) {
        edgeDeviation = Math.max(
          edgeDeviation,
          distanceToSegmentMeters(source.vertices[sourceIndex], left, right),
        );
        sourceIndex += 1;
      }
      if (sourceIndex >= source.vertices.length) throw new Error("local viewport vertex is not a source vertex");
      sourceIndex += 1;
      evaluatedEdges += 1;
      maximumDeviationMeters = Math.max(maximumDeviationMeters, edgeDeviation);
      if (edgeDeviation > tolerance + 2) violatingEdges += 1;
    }
  }
  return {
    zoom,
    vertices: response.visibleVertexCount,
    paths: response.paths.length,
    evaluatedEdges,
    maximumDeviationMeters: Math.round(maximumDeviationMeters),
    toleranceMeters: Math.round(tolerance),
    violatingEdges,
  };
}

const last = preview.paths.at(-1)?.vertices.at(-1);
if (!last) throw new Error("preview has no trajectory vertices");
const localViews = [
  [9, 1, 0.65],
  [11, 0.25, 0.15],
  [14, 0.045, 0.03],
];
const reports = {
  world: [7, 9, 11, 14].map(auditZoom),
  local: localViews.map(([zoom, halfWidth, halfHeight]) => auditLocalZoom(zoom, [
    last[1] - halfWidth,
    last[2] - halfHeight,
    last[1] + halfWidth,
    last[2] + halfHeight,
  ])),
};
console.log(JSON.stringify(reports, null, 2));
if ([...reports.world, ...reports.local].some((report) => report.violatingEdges > 0)) process.exitCode = 1;
