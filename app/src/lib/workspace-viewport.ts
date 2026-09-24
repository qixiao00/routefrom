import {
  haversineMeters,
  selectPaths,
  selectedDistanceMeters,
  type PreviewModeLeg,
  type PreviewPath,
  type PreviewVertex,
  type SelectedPath,
} from "./workspace-data.ts";
import { normalizeTimeRanges, type TimeRange } from "./workspace-query.ts";

export type ViewportBounds = [west: number, south: number, east: number, north: number];

export interface ViewportRequest {
  datasetId: string;
  processingRunId: string;
  ranges: TimeRange[];
  bounds: ViewportBounds;
  detailZoom: number;
}

export interface ViewportResponse {
  paths: ViewportPath[];
  coverageBounds: ViewportBounds;
  detailZoom: number;
  selectedBounds: ViewportBounds | null;
  selectedDistanceMeters: number;
  visibleVertexCount: number;
}

export interface IndexedSelectedPath {
  path: ViewportPath;
  bounds: ViewportBounds;
}

export interface ViewportSelection {
  paths: IndexedSelectedPath[];
  bounds: ViewportBounds | null;
  distanceMeters: number;
}

export interface ViewportPath extends SelectedPath {
  movementClass: "ordinary" | "sparse" | "high_speed";
}

export class ViewportQueryError extends Error {}

const MAX_RANGES = 64;
const OFFSET_INSTANT = /(Z|[+-]\d{2}:\d{2})$/i;

export function parseViewportRequest(value: unknown): ViewportRequest {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new ViewportQueryError("request body must be an object");
  }
  const input = value as Record<string, unknown>;
  if (
    typeof input.datasetId !== "string" ||
    typeof input.processingRunId !== "string" ||
    input.datasetId.length > 100 ||
    input.processingRunId.length > 100
  ) {
    throw new ViewportQueryError("dataset or processing run is invalid");
  }
  if (!Array.isArray(input.ranges) || input.ranges.length < 1 || input.ranges.length > MAX_RANGES) {
    throw new ViewportQueryError("ranges must contain 1–64 time intervals");
  }
  const ranges = input.ranges.map((candidate, index) => {
    if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) {
      throw new ViewportQueryError(`ranges[${index}] is invalid`);
    }
    const range = candidate as Record<string, unknown>;
    if (
      typeof range.start !== "string" ||
      typeof range.end !== "string" ||
      !OFFSET_INSTANT.test(range.start) ||
      !OFFSET_INSTANT.test(range.end)
    ) {
      throw new ViewportQueryError(`ranges[${index}] must contain timestamps`);
    }
    const start = Date.parse(range.start);
    const end = Date.parse(range.end);
    if (!Number.isFinite(start) || !Number.isFinite(end) || start >= end) {
      throw new ViewportQueryError(`ranges[${index}] is not a valid interval`);
    }
    return { start: new Date(start).toISOString(), end: new Date(end).toISOString() };
  });
  if (!Array.isArray(input.bounds) || input.bounds.length !== 4) {
    throw new ViewportQueryError("bounds must be [west, south, east, north]");
  }
  const bounds = input.bounds as number[];
  if (
    bounds.some((coordinate) => typeof coordinate !== "number" || !Number.isFinite(coordinate)) ||
    bounds[0] < -180 || bounds[2] > 180 || bounds[1] < -90 || bounds[3] > 90 ||
    bounds[0] >= bounds[2] || bounds[1] >= bounds[3]
  ) {
    throw new ViewportQueryError("bounds are invalid");
  }
  if (
    typeof input.detailZoom !== "number" ||
    !Number.isInteger(input.detailZoom) ||
    input.detailZoom < 0 ||
    input.detailZoom > 22
  ) {
    throw new ViewportQueryError("detailZoom must be an integer between 0 and 22");
  }
  return {
    datasetId: input.datasetId,
    processingRunId: input.processingRunId,
    ranges: normalizeTimeRanges(ranges),
    bounds: bounds as ViewportBounds,
    detailZoom: input.detailZoom,
  };
}

function boundsForVertices(vertices: readonly PreviewVertex[]): ViewportBounds {
  let west = Infinity;
  let south = Infinity;
  let east = -Infinity;
  let north = -Infinity;
  for (const vertex of vertices) {
    west = Math.min(west, vertex[1]);
    south = Math.min(south, vertex[2]);
    east = Math.max(east, vertex[1]);
    north = Math.max(north, vertex[2]);
  }
  return [west, south, east, north];
}

function boundsOverlap(left: ViewportBounds, right: ViewportBounds): boolean {
  return left[0] <= right[2] && left[2] >= right[0] && left[1] <= right[3] && left[3] >= right[1];
}

function boundsContain(outer: ViewportBounds, inner: ViewportBounds): boolean {
  return outer[0] <= inner[0] && outer[1] <= inner[1] &&
    outer[2] >= inner[2] && outer[3] >= inner[3];
}

function distanceToSegment(
  point: readonly number[],
  start: readonly number[],
  end: readonly number[],
): number {
  const dx = end[0] - start[0];
  const dy = end[1] - start[1];
  const lengthSquared = dx * dx + dy * dy;
  if (lengthSquared <= 1e-18) return Math.hypot(point[0] - start[0], point[1] - start[1]);
  const fraction = Math.max(0, Math.min(1,
    ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / lengthSquared,
  ));
  return Math.hypot(
    point[0] - (start[0] + fraction * dx),
    point[1] - (start[1] + fraction * dy),
  );
}

function withLocalImportance(vertices: readonly PreviewVertex[]): PreviewVertex[] {
  if (vertices.length <= 2) return vertices.map((vertex) => [vertex[0], vertex[1], vertex[2], null]);
  const centerLatitude = vertices.reduce((sum, vertex) => sum + vertex[2], 0) / vertices.length;
  const centerLongitude = vertices.reduce((sum, vertex) => sum + vertex[1], 0) / vertices.length;
  const radians = Math.PI / 180;
  const scaleX = 6_371_008.8 * radians * Math.cos(centerLatitude * radians);
  const scaleY = 6_371_008.8 * radians;
  const projected = vertices.map((vertex) => [
    (vertex[1] - centerLongitude) * scaleX,
    (vertex[2] - centerLatitude) * scaleY,
  ]);
  const scores: Array<number | null> = vertices.map((vertex) => vertex[3] == null ? null : 0);
  scores[0] = null;
  scores[scores.length - 1] = null;
  const anchors = scores.flatMap((score, index) => score === null ? [index] : []);
  for (let anchorIndex = 0; anchorIndex + 1 < anchors.length; anchorIndex += 1) {
    const start = anchors[anchorIndex];
    const end = anchors[anchorIndex + 1];
    const pending: Array<[number, number, boolean]> = [[start, end, false]];
    const splits = new Map<string, { split: number; deviation: number }>();
    const effective = new Map<string, number>();
    while (pending.length) {
      const [left, right, visited] = pending.pop()!;
      if (right - left <= 1) continue;
      const key = `${left}:${right}`;
      if (visited) {
        const { split, deviation } = splits.get(key)!;
        const score = Math.max(
          deviation,
          effective.get(`${left}:${split}`) ?? 0,
          effective.get(`${split}:${right}`) ?? 0,
        );
        scores[split] = score;
        effective.set(key, score);
        continue;
      }
      let split = left + 1;
      let deviation = -1;
      for (let index = left + 1; index < right; index += 1) {
        const current = distanceToSegment(projected[index], projected[left], projected[right]);
        if (current > deviation) {
          deviation = current;
          split = index;
        }
      }
      if (deviation <= 1e-9) continue;
      splits.set(key, { split, deviation });
      pending.push([left, right, true], [left, split, false], [split, right, false]);
    }
  }
  return vertices.map((vertex, index) => [vertex[0], vertex[1], vertex[2], scores[index]]);
}

function airIntervals(legs: readonly PreviewModeLeg[]): Array<[number, number]> {
  const sorted = legs
    .filter((leg) => leg.mode === "air")
    .map((leg): [number, number] => [Date.parse(leg.start), Date.parse(leg.end)])
    .sort((left, right) => left[0] - right[0]);
  const merged: Array<[number, number]> = [];
  for (const interval of sorted) {
    const previous = merged.at(-1);
    if (previous && interval[0] <= previous[1]) previous[1] = Math.max(previous[1], interval[1]);
    else merged.push(interval);
  }
  return merged;
}

function isAirTime(instant: number, intervals: readonly [number, number][]): boolean {
  let lower = 0;
  let upper = intervals.length;
  while (lower < upper) {
    const middle = (lower + upper) >>> 1;
    if (intervals[middle][0] <= instant) lower = middle + 1;
    else upper = middle;
  }
  return lower > 0 && instant < intervals[lower - 1][1];
}

function splitByMovementClass(
  path: SelectedPath,
  intervals: readonly [number, number][],
): ViewportPath[] {
  const pieces: ViewportPath[] = [];
  let current: ViewportPath | null = null;
  for (let index = 0; index + 1 < path.vertices.length; index += 1) {
    const left = path.vertices[index];
    const right = path.vertices[index + 1];
    const start = Date.parse(left[0]);
    const end = Date.parse(right[0]);
    const displacementMeters = haversineMeters(left, right);
    const speed = end > start ? displacementMeters / ((end - start) / 1000) : 0;
    const movementClass = isAirTime((start + end) / 2, intervals) || speed >= 70
      ? "high_speed"
      : displacementMeters >= 2_000 ? "sparse" : "ordinary";
    if (!current || current.movementClass !== movementClass) {
      current = { ...path, movementClass, vertices: [left, right] };
      pieces.push(current);
    } else {
      current.vertices.push(right);
    }
  }
  return pieces.map((piece) => ({ ...piece, vertices: withLocalImportance(piece.vertices) }));
}

export function buildViewportSelection(
  paths: readonly PreviewPath[],
  ranges: readonly TimeRange[],
  modeLegs: readonly PreviewModeLeg[] = [],
): ViewportSelection {
  const selected = selectPaths(paths, ranges);
  const indexed: IndexedSelectedPath[] = [];
  let bounds: ViewportBounds | null = null;
  const intervals = airIntervals(modeLegs);
  for (const path of selected) {
    for (const piece of splitByMovementClass(path, intervals)) {
      const pathBounds = boundsForVertices(piece.vertices);
      indexed.push({ path: piece, bounds: pathBounds });
      bounds = bounds
        ? [
            Math.min(bounds[0], pathBounds[0]),
            Math.min(bounds[1], pathBounds[1]),
            Math.max(bounds[2], pathBounds[2]),
            Math.max(bounds[3], pathBounds[3]),
          ]
        : pathBounds;
    }
  }
  return { paths: indexed, bounds, distanceMeters: selectedDistanceMeters(selected) };
}

function edgeIntersectsBounds(
  left: PreviewVertex,
  right: PreviewVertex,
  bounds: ViewportBounds,
): boolean {
  const dx = right[1] - left[1];
  const dy = right[2] - left[2];
  let entry = 0;
  let exit = 1;
  const edges = [
    [-dx, left[1] - bounds[0]],
    [dx, bounds[2] - left[1]],
    [-dy, left[2] - bounds[1]],
    [dy, bounds[3] - left[2]],
  ];
  for (const [direction, offset] of edges) {
    if (direction === 0) {
      if (offset < 0) return false;
      continue;
    }
    const fraction = offset / direction;
    if (direction < 0) entry = Math.max(entry, fraction);
    else exit = Math.min(exit, fraction);
    if (entry > exit) return false;
  }
  return true;
}

function clipPathToBounds(path: ViewportPath, bounds: ViewportBounds): ViewportPath[] {
  const pieces: ViewportPath[] = [];
  let vertices: PreviewVertex[] = [];
  for (let index = 0; index + 1 < path.vertices.length; index += 1) {
    const left = path.vertices[index];
    const right = path.vertices[index + 1];
    if (edgeIntersectsBounds(left, right, bounds)) {
      if (vertices.length === 0) vertices.push(left);
      vertices.push(right);
    } else if (vertices.length > 1) {
      pieces.push({ ...path, vertices });
      vertices = [];
    }
  }
  if (vertices.length > 1) pieces.push({ ...path, vertices });
  return pieces;
}

function toleranceMeters(bounds: ViewportBounds, zoom: number): number {
  const latitude = Math.max(-85, Math.min(85, (bounds[1] + bounds[3]) / 2));
  return (156_543.03 * Math.cos((latitude * Math.PI) / 180) / 2 ** zoom) * 0.8;
}

export function queryViewportSelection(
  selection: ViewportSelection,
  bounds: ViewportBounds,
  detailZoom: number,
  maxVertices = Number.POSITIVE_INFINITY,
): ViewportResponse {
  const pieces = selection.paths
    .filter((item) => boundsOverlap(item.bounds, bounds))
    .flatMap((item) => boundsContain(bounds, item.bounds)
      ? [item.path]
      : clipPathToBounds(item.path, bounds).map((piece) => ({
          ...piece,
          vertices: withLocalImportance(piece.vertices),
        })));
  const tolerance = toleranceMeters(bounds, detailZoom);
  const mandatory = (path: ViewportPath, index: number) =>
    index === 0 || index === path.vertices.length - 1 || path.vertices[index][3] == null;
  const optional: Array<{ importance: number; pathIndex: number; vertexIndex: number }> = [];
  pieces.forEach((path, pathIndex) => {
    path.vertices.forEach((vertex, vertexIndex) => {
      if (!mandatory(path, vertexIndex) && (vertex[3] ?? 0) >= tolerance) {
        optional.push({ importance: vertex[3] ?? 0, pathIndex, vertexIndex });
      }
    });
  });
  const mandatoryCount = pieces.reduce(
    (total, path) => total + path.vertices.filter((_, index) => mandatory(path, index)).length,
    0,
  );
  const optionalAllowance = Math.max(0, maxVertices - mandatoryCount);
  let selectedOptional: Set<string> | null = null;
  if (optional.length > optionalAllowance) {
    optional.sort((left, right) =>
      right.importance - left.importance ||
      left.pathIndex - right.pathIndex ||
      left.vertexIndex - right.vertexIndex
    );
    selectedOptional = new Set(optional.slice(0, optionalAllowance).map(
      (item) => `${item.pathIndex}:${item.vertexIndex}`,
    ));
  }
  const paths = pieces.map((path, pathIndex) => ({
    ...path,
    vertices: path.vertices.filter(
      (vertex, index) => mandatory(path, index) ||
        (selectedOptional
          ? selectedOptional.has(`${pathIndex}:${index}`)
          : (vertex[3] ?? 0) >= tolerance),
    ),
  }));
  return {
    paths,
    coverageBounds: bounds,
    detailZoom,
    selectedBounds: selection.bounds,
    selectedDistanceMeters: selection.distanceMeters,
    visibleVertexCount: paths.reduce((sum, path) => sum + path.vertices.length, 0),
  };
}
