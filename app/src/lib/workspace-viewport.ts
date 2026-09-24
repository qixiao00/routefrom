import {
  selectPaths,
  selectedDistanceMeters,
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
  paths: SelectedPath[];
  coverageBounds: ViewportBounds;
  detailZoom: number;
  selectedBounds: ViewportBounds | null;
  selectedDistanceMeters: number;
  visibleVertexCount: number;
}

export interface IndexedSelectedPath {
  path: SelectedPath;
  bounds: ViewportBounds;
}

export interface ViewportSelection {
  paths: IndexedSelectedPath[];
  bounds: ViewportBounds | null;
  distanceMeters: number;
}

export class ViewportQueryError extends Error {}

const MAX_RANGES = 64;
const MAX_VISIBLE_VERTICES = 12_000;
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

export function buildViewportSelection(
  paths: readonly PreviewPath[],
  ranges: readonly TimeRange[],
): ViewportSelection {
  const selected = selectPaths(paths, ranges);
  const indexed: IndexedSelectedPath[] = [];
  let bounds: ViewportBounds | null = null;
  for (const path of selected) {
    const pathBounds = boundsForVertices(path.vertices);
    indexed.push({ path, bounds: pathBounds });
    bounds = bounds
      ? [
          Math.min(bounds[0], pathBounds[0]),
          Math.min(bounds[1], pathBounds[1]),
          Math.max(bounds[2], pathBounds[2]),
          Math.max(bounds[3], pathBounds[3]),
        ]
      : pathBounds;
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

function clipPathToBounds(path: SelectedPath, bounds: ViewportBounds): SelectedPath[] {
  const pieces: SelectedPath[] = [];
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
  maxVertices = MAX_VISIBLE_VERTICES,
): ViewportResponse {
  const pieces = selection.paths
    .filter((item) => boundsOverlap(item.bounds, bounds))
    .flatMap((item) => clipPathToBounds(item.path, bounds));
  const tolerance = toleranceMeters(bounds, detailZoom);
  const mandatory = (path: SelectedPath, index: number) =>
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
