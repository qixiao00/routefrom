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
  reasonCodes?: string[];
  samplingContext?: "moving" | "stationary" | "uncertain" | null;
  normalWaitProbability?: number | null;
  expectedIntervalSeconds?: number | null;
  startPosition: [number, number];
  endPosition: [number, number];
}

export interface PreviewInferredConnection {
  id: string;
  gapId: string;
  start: string;
  end: string;
  kind: "same_place" | "straight_line_context";
  confidence: number;
  displayable: boolean;
  startPosition: [number, number];
  endPosition: [number, number];
  reasonCodes: string[];
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
  suggestedMapView?: {
    longitude: number;
    latitude: number;
    zoom: number;
    pitch: number;
    bearing: number;
  } | null;
  paths: PreviewPath[];
  gaps: PreviewGap[];
  inferredConnections?: PreviewInferredConnection[];
  stays: PreviewStay[];
  trips: PreviewTrip[];
  modeLegs: PreviewModeLeg[];
  places: PreviewPlace[];
}

export type WorkspaceEvents = Pick<WorkspacePreview, "gaps" | "inferredConnections" | "stays" | "trips" | "modeLegs">;

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
      if (
        path.vertices.length < 2 ||
        Date.parse(path.vertices.at(-1)![0]) < rangeStart ||
        Date.parse(path.vertices[0][0]) >= rangeEnd
      ) continue;
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

export function gapExplanation(gap: PreviewGap): string {
  const reasons = gap.reasonCodes ?? [];
  if (reasons.includes("return_conflicts_with_reported_speeds")) {
    return "短时往返与设备记录的速度矛盾，两端不能当作确认路线连接。";
  }
  if (reasons.includes("short_move_with_weak_position_support")) {
    return "这段位移的定位精度不足，保留观测点但不推断中间路线。";
  }
  if (reasons.includes("fast_return_without_sensor_support")) {
    return "短时快速折返缺少传感器速度支持，路线保持未知。";
  }
  if (reasons.includes("uncorroborated_displacement")) {
    return "大位移缺少设备速度和相邻运动的佐证，暂不确认为连续路线。";
  }
  if (gap.cause === "continuity_failure") {
    return "前后观测缺乏可靠的连续性证据，未将它们合成路线。";
  }
  if (gap.cause === "excluded_block") {
    return "中间观测被判为异常，前后路线仍无法可靠接续。";
  }
  if (gap.cause === "clock_discontinuity") {
    return "时间记录存在不连续，无法确定这一段的先后路线。";
  }
  return "这段时间没有足迹观测，可能是手机未携带、关机或系统未采样。";
}

export function drawableInferredConnections(
  connections: readonly PreviewInferredConnection[],
  ranges: readonly TimeRange[],
): PreviewInferredConnection[] {
  return connections.filter((connection) =>
    connection.displayable &&
    connection.kind === "straight_line_context" &&
    inferredConnectionWithinSelection(connection, ranges)
  );
}

export function inferredConnectionWithinSelection(
  connection: PreviewInferredConnection,
  ranges: readonly TimeRange[],
): boolean {
  const start = Date.parse(connection.start);
  const end = Date.parse(connection.end);
  return ranges.some((range) =>
    Date.parse(range.start) <= start && end < Date.parse(range.end)
  );
}

export function haversineMeters(left: PreviewVertex, right: PreviewVertex): number {
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
        (distance, vertex, index) => distance + haversineMeters(path.vertices[index], vertex),
        0,
      ),
    0,
  );
}
