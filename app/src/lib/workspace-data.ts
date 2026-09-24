import type { TimeRange } from "./workspace-query";

export type PreviewVertex = [
  recordedAt: string,
  longitude: number,
  latitude: number,
  importanceMeters?: number | null,
];

export interface PreviewPath {
  segmentIndex: number;
  vertices: PreviewVertex[];
}

export interface PreviewGap {
  id: string;
  start: string;
  end: string;
  cause: string;
  confidence: number;
  samplingContext?: "moving" | "stationary" | "uncertain" | null;
  normalWaitProbability?: number | null;
  expectedIntervalSeconds?: number | null;
  startPosition: [number, number];
  endPosition: [number, number];
}

export interface PreviewStay {
  id: string;
  start: string;
  end: string;
  kind: "visit" | "transport_pause" | "uncertain_stop";
  position: [number, number];
  confidence: number;
  visitProbability: number;
  durationSeconds: number;
}

export interface PreviewTrip {
  id: string;
  start: string;
  end: string;
  boundary: string;
  confidence: number;
  distanceMeters: number;
  unknownSeconds: number;
}

export interface PreviewModeLeg {
  id: string;
  start: string;
  end: string;
  mode: string;
  level: string;
  confidence: number;
}

export interface PreviewPlace {
  id: string;
  name: string;
  position: [number, number];
  visitCount: number;
  dwellSeconds: number;
  confidence: number;
  frequentProbability: number;
  firstVisitedAt: string;
  lastVisitedAt: string;
}

export interface WorkspacePreview {
  schemaVersion: 1;
  dataset: {
    id: string;
    name: string;
    sourceName: string;
    pointCount: number;
    startedAt: string;
    endedAt: string;
  };
  processing: {
    runId: string;
    algorithmVersion: string;
    trajectoryVariant: "cleaned_gps" | "smoothed_gps" | "map_matched";
    smoothingConfidence: number;
  };
  summary: {
    gapCount: number;
    stayCount: number;
    tripCount: number;
    placeCount: number;
    modeLegCount: number;
  };
  suggestedRanges: TimeRange[];
  paths: PreviewPath[];
  gaps: PreviewGap[];
  stays: PreviewStay[];
  trips: PreviewTrip[];
  modeLegs: PreviewModeLeg[];
  places: PreviewPlace[];
}

export interface SelectedPath extends PreviewPath {
  rangeIndex: number;
}

function interpolate(left: PreviewVertex, right: PreviewVertex, instant: number): PreviewVertex {
  const leftTime = Date.parse(left[0]);
  const rightTime = Date.parse(right[0]);
  if (instant === leftTime) return left;
  if (instant === rightTime) return right;
  const fraction = rightTime === leftTime ? 0 : (instant - leftTime) / (rightTime - leftTime);
  return [
    new Date(instant).toISOString(),
    left[1] + (right[1] - left[1]) * fraction,
    left[2] + (right[2] - left[2]) * fraction,
    null,
  ];
}

export function simplifySelectedPath(
  path: SelectedPath,
  toleranceMeters: number,
): PreviewVertex[] {
  if (path.vertices.length <= 2 || toleranceMeters <= 0) return path.vertices;
  return path.vertices.filter(
    (vertex, index) =>
      index === 0 ||
      index === path.vertices.length - 1 ||
      vertex[3] == null ||
      vertex[3] >= toleranceMeters,
  );
}

export function selectPaths(paths: readonly PreviewPath[], ranges: readonly TimeRange[]): SelectedPath[] {
  const selected: SelectedPath[] = [];
  ranges.forEach((range, rangeIndex) => {
    const rangeStart = Date.parse(range.start);
    const rangeEnd = Date.parse(range.end);
    for (const path of paths) {
      const vertices: PreviewVertex[] = [];
      for (let index = 0; index + 1 < path.vertices.length; index += 1) {
        const left = path.vertices[index];
        const right = path.vertices[index + 1];
        const leftTime = Date.parse(left[0]);
        const rightTime = Date.parse(right[0]);
        if (rightTime < rangeStart || leftTime >= rangeEnd) continue;
        const start = Math.max(leftTime, rangeStart);
        const end = Math.min(rightTime, rangeEnd);
        if (start > end) continue;
        const startVertex = interpolate(left, right, start);
        const endVertex = interpolate(left, right, end);
        if (vertices.at(-1)?.[0] !== startVertex[0]) vertices.push(startVertex);
        if (vertices.at(-1)?.[0] !== endVertex[0]) vertices.push(endVertex);
      }
      if (vertices.length > 1) selected.push({ ...path, rangeIndex, vertices });
    }
  });
  return selected;
}

export function overlapsSelection(
  start: string,
  end: string,
  ranges: readonly TimeRange[],
): boolean {
  return ranges.some((range) => start < range.end && end > range.start);
}

function haversine(left: PreviewVertex, right: PreviewVertex): number {
  const radius = 6_371_008.8;
  const latitudeA = (left[2] * Math.PI) / 180;
  const latitudeB = (right[2] * Math.PI) / 180;
  const deltaLatitude = latitudeB - latitudeA;
  const deltaLongitude = ((right[1] - left[1]) * Math.PI) / 180;
  const value =
    Math.sin(deltaLatitude / 2) ** 2 +
    Math.cos(latitudeA) * Math.cos(latitudeB) * Math.sin(deltaLongitude / 2) ** 2;
  return 2 * radius * Math.asin(Math.min(1, Math.sqrt(value)));
}

export function selectedDistanceMeters(paths: readonly SelectedPath[]): number {
  return paths.reduce(
    (total, path) =>
      total +
      path.vertices.slice(1).reduce(
        (distance, vertex, index) => distance + haversine(path.vertices[index], vertex),
        0,
      ),
    0,
  );
}
