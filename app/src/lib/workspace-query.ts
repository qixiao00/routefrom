export const MAX_QUERY_RANGES = 64;
export const MAX_VERTEX_BUDGET = 250_000;

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const OFFSET_INSTANT_PATTERN = /(Z|[+-]\d{2}:\d{2})$/i;
const ENTITY_KINDS = new Set(["point", "gap", "visit", "trip", "leg", "place"]);

export type EntityKind = "point" | "gap" | "visit" | "trip" | "leg" | "place";

export interface TimeRange {
  start: string;
  end: string;
}

export interface WorkspaceQuery {
  datasetId: string;
  processingRunId: "active" | string;
  timeSelection: {
    timezone: string;
    ranges: TimeRange[];
  };
  trajectoryDetail: {
    pixelToleranceMeters: number;
    vertexBudget?: number;
  };
  entityKinds: EntityKind[];
}

export interface TrajectoryVertexDto {
  recordedAt: string;
  coordinates: [number, number];
  sourcePointId: string | null;
  importanceMeters: number | null;
  isInterpolated: boolean;
  anchorReasons: string[];
}

export interface TrajectoryPathDto {
  selectionRangeIndex: number;
  segmentIndex: number;
  selectedRange: TimeRange;
  variantKind: "cleaned_gps" | "smoothed_gps" | "map_matched";
  isInferred: false;
  appliedImportanceThresholdMeters: number;
  vertices: TrajectoryVertexDto[];
}

export class WorkspaceQueryValidationError extends Error {}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function rejectUnknownKeys(
  value: Record<string, unknown>,
  allowed: readonly string[],
  path: string,
): void {
  const allowedKeys = new Set(allowed);
  const unknown = Object.keys(value).filter((key) => !allowedKeys.has(key));
  if (unknown.length > 0) {
    throw new WorkspaceQueryValidationError(`${path} contains unknown field: ${unknown[0]}`);
  }
}

function parseInstant(value: unknown, path: string): string {
  if (typeof value !== "string" || !OFFSET_INSTANT_PATTERN.test(value)) {
    throw new WorkspaceQueryValidationError(`${path} must be an ISO instant with a UTC offset`);
  }
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) {
    throw new WorkspaceQueryValidationError(`${path} is not a valid instant`);
  }
  return new Date(timestamp).toISOString();
}

function parseTimezone(value: unknown): string {
  if (typeof value !== "string" || value.length === 0 || value.length > 100) {
    throw new WorkspaceQueryValidationError("timeSelection.timezone is invalid");
  }
  try {
    new Intl.DateTimeFormat("en", { timeZone: value }).format();
  } catch {
    throw new WorkspaceQueryValidationError("timeSelection.timezone is not recognized");
  }
  return value;
}

export function normalizeTimeRanges(ranges: readonly TimeRange[]): TimeRange[] {
  const ordered = [...ranges].sort((left, right) => left.start.localeCompare(right.start));
  const normalized: TimeRange[] = [];
  for (const current of ordered) {
    const previous = normalized.at(-1);
    if (previous && current.start <= previous.end) {
      previous.end = current.end > previous.end ? current.end : previous.end;
    } else {
      normalized.push({ ...current });
    }
  }
  return normalized;
}

function parseRanges(value: unknown): TimeRange[] {
  if (!Array.isArray(value) || value.length === 0) {
    throw new WorkspaceQueryValidationError("timeSelection.ranges must not be empty");
  }
  if (value.length > MAX_QUERY_RANGES) {
    throw new WorkspaceQueryValidationError(
      `timeSelection.ranges cannot contain more than ${MAX_QUERY_RANGES} ranges`,
    );
  }
  const ranges = value.map((candidate, index) => {
    if (!isRecord(candidate)) {
      throw new WorkspaceQueryValidationError(`timeSelection.ranges[${index}] must be an object`);
    }
    rejectUnknownKeys(candidate, ["start", "end"], `timeSelection.ranges[${index}]`);
    const start = parseInstant(candidate.start, `timeSelection.ranges[${index}].start`);
    const end = parseInstant(candidate.end, `timeSelection.ranges[${index}].end`);
    if (start >= end) {
      throw new WorkspaceQueryValidationError(
        `timeSelection.ranges[${index}] must use a non-empty [start, end) interval`,
      );
    }
    return { start, end };
  });
  return normalizeTimeRanges(ranges);
}

function parseTrajectoryDetail(value: unknown): WorkspaceQuery["trajectoryDetail"] {
  if (value === undefined) {
    return { pixelToleranceMeters: 0 };
  }
  if (!isRecord(value)) {
    throw new WorkspaceQueryValidationError("trajectoryDetail must be an object");
  }
  rejectUnknownKeys(value, ["pixelToleranceMeters", "vertexBudget"], "trajectoryDetail");
  const tolerance = value.pixelToleranceMeters ?? 0;
  if (typeof tolerance !== "number" || !Number.isFinite(tolerance) || tolerance < 0) {
    throw new WorkspaceQueryValidationError("trajectoryDetail.pixelToleranceMeters is invalid");
  }
  const budget = value.vertexBudget;
  if (
    budget !== undefined &&
    (typeof budget !== "number" ||
      !Number.isInteger(budget) ||
      budget < 1 ||
      budget > MAX_VERTEX_BUDGET)
  ) {
    throw new WorkspaceQueryValidationError(
      `trajectoryDetail.vertexBudget must be between 1 and ${MAX_VERTEX_BUDGET}`,
    );
  }
  return {
    pixelToleranceMeters: tolerance,
    ...(budget === undefined ? {} : { vertexBudget: budget }),
  };
}

function parseEntityKinds(value: unknown): EntityKind[] {
  if (value === undefined) {
    return ["point", "gap", "visit", "trip", "leg", "place"];
  }
  if (!Array.isArray(value) || new Set(value).size !== value.length) {
    throw new WorkspaceQueryValidationError("entityKinds must contain unique values");
  }
  for (const kind of value) {
    if (typeof kind !== "string" || !ENTITY_KINDS.has(kind)) {
      throw new WorkspaceQueryValidationError(`unsupported entity kind: ${String(kind)}`);
    }
  }
  return value as EntityKind[];
}

export function parseWorkspaceQuery(value: unknown): WorkspaceQuery {
  if (!isRecord(value)) {
    throw new WorkspaceQueryValidationError("request body must be an object");
  }
  rejectUnknownKeys(
    value,
    ["datasetId", "processingRunId", "timeSelection", "trajectoryDetail", "entityKinds"],
    "request body",
  );
  if (typeof value.datasetId !== "string" || !UUID_PATTERN.test(value.datasetId)) {
    throw new WorkspaceQueryValidationError("datasetId must be a UUID");
  }
  if (
    typeof value.processingRunId !== "string" ||
    (value.processingRunId !== "active" && !UUID_PATTERN.test(value.processingRunId))
  ) {
    throw new WorkspaceQueryValidationError("processingRunId must be active or a UUID");
  }
  if (!isRecord(value.timeSelection)) {
    throw new WorkspaceQueryValidationError("timeSelection must be an object");
  }
  rejectUnknownKeys(value.timeSelection, ["timezone", "ranges"], "timeSelection");
  return {
    datasetId: value.datasetId.toLowerCase(),
    processingRunId:
      value.processingRunId === "active" ? "active" : value.processingRunId.toLowerCase(),
    timeSelection: {
      timezone: parseTimezone(value.timeSelection.timezone),
      ranges: parseRanges(value.timeSelection.ranges),
    },
    trajectoryDetail: parseTrajectoryDetail(value.trajectoryDetail),
    entityKinds: parseEntityKinds(value.entityKinds),
  };
}

export interface StoredTrajectoryVertex {
  selectionRangeIndex: number;
  segmentIndex: number;
  sequenceNumber: number;
  recordedAt: string;
  longitude: number;
  latitude: number;
  sourcePointId: string | null;
  importanceMeters: number | null;
  anchorReasons: string[];
}

function interpolate(
  left: TrajectoryVertexDto,
  right: TrajectoryVertexDto,
  recordedAt: string,
  reason: string,
): TrajectoryVertexDto {
  if (recordedAt === left.recordedAt) return left;
  if (recordedAt === right.recordedAt) return right;
  const leftTime = Date.parse(left.recordedAt);
  const fraction = (Date.parse(recordedAt) - leftTime) / (Date.parse(right.recordedAt) - leftTime);
  return {
    recordedAt,
    coordinates: [
      left.coordinates[0] + (right.coordinates[0] - left.coordinates[0]) * fraction,
      left.coordinates[1] + (right.coordinates[1] - left.coordinates[1]) * fraction,
    ],
    sourcePointId: null,
    importanceMeters: null,
    isInterpolated: true,
    anchorReasons: [reason],
  };
}

function appendDistinct(vertices: TrajectoryVertexDto[], vertex: TrajectoryVertexDto): void {
  const previous = vertices.at(-1);
  if (previous?.recordedAt === vertex.recordedAt) {
    if (vertex.isInterpolated && !previous.isInterpolated) return;
    vertices[vertices.length - 1] = vertex;
  } else {
    vertices.push(vertex);
  }
}

function clipVertices(vertices: readonly TrajectoryVertexDto[], range: TimeRange): TrajectoryVertexDto[] {
  if (vertices.length === 1) {
    return vertices[0].recordedAt >= range.start && vertices[0].recordedAt < range.end
      ? [vertices[0]]
      : [];
  }
  const clipped: TrajectoryVertexDto[] = [];
  for (let index = 0; index + 1 < vertices.length; index += 1) {
    const left = vertices[index];
    const right = vertices[index + 1];
    if (right.recordedAt < range.start || left.recordedAt >= range.end) continue;
    const intersectionStart = left.recordedAt > range.start ? left.recordedAt : range.start;
    const intersectionEnd = right.recordedAt < range.end ? right.recordedAt : range.end;
    if (intersectionStart > intersectionEnd) continue;
    appendDistinct(clipped, interpolate(left, right, intersectionStart, "query_range_start"));
    appendDistinct(clipped, interpolate(left, right, intersectionEnd, "query_range_end"));
  }
  return clipped;
}

function isMandatory(vertex: TrajectoryVertexDto, position: number, length: number): boolean {
  return (
    position === 0 ||
    position === length - 1 ||
    vertex.anchorReasons.length > 0 ||
    vertex.importanceMeters === null
  );
}

function simplifyPaths(
  paths: TrajectoryVertexDto[][],
  tolerance: number,
  budget: number | undefined,
): { threshold: number; paths: TrajectoryVertexDto[][] } {
  if (budget === undefined) {
    return {
      threshold: tolerance,
      paths: paths.map((path) =>
        path.filter(
          (vertex, position) =>
            isMandatory(vertex, position, path.length) || (vertex.importanceMeters ?? 0) >= tolerance,
        ),
      ),
    };
  }
  const selected = new Set<string>();
  let mandatoryCount = 0;
  const optional: Array<{ importance: number; pathIndex: number; position: number }> = [];
  paths.forEach((path, pathIndex) => {
    path.forEach((vertex, position) => {
      if (isMandatory(vertex, position, path.length)) {
        mandatoryCount += 1;
      } else if ((vertex.importanceMeters ?? 0) >= tolerance) {
        optional.push({ importance: vertex.importanceMeters ?? 0, pathIndex, position });
      }
    });
  });
  optional.sort(
    (left, right) =>
      right.importance - left.importance || left.pathIndex - right.pathIndex || left.position - right.position,
  );
  const allowedOptional = Math.max(0, budget - mandatoryCount);
  optional.slice(0, allowedOptional).forEach((item) => selected.add(`${item.pathIndex}:${item.position}`));
  const chosen = optional.slice(0, allowedOptional);
  const threshold = chosen.length > 0 ? Math.max(tolerance, chosen.at(-1)!.importance) : tolerance;
  return {
    threshold,
    paths: paths.map((path, pathIndex) =>
      path.filter(
        (vertex, position) =>
          isMandatory(vertex, position, path.length) || selected.has(`${pathIndex}:${position}`),
      ),
    ),
  };
}

export function buildTrajectoryPaths(
  rows: readonly StoredTrajectoryVertex[],
  ranges: readonly TimeRange[],
  variantKind: TrajectoryPathDto["variantKind"],
  detail: WorkspaceQuery["trajectoryDetail"],
): TrajectoryPathDto[] {
  const grouped = new Map<string, StoredTrajectoryVertex[]>();
  for (const row of rows) {
    const key = `${row.selectionRangeIndex}:${row.segmentIndex}`;
    const group = grouped.get(key) ?? [];
    group.push(row);
    grouped.set(key, group);
  }
  const metadata: Array<{ rangeIndex: number; segmentIndex: number }> = [];
  const clippedPaths: TrajectoryVertexDto[][] = [];
  const orderedGroups = [...grouped.values()].sort(
    (left, right) =>
      left[0].selectionRangeIndex - right[0].selectionRangeIndex ||
      left[0].segmentIndex - right[0].segmentIndex,
  );
  for (const group of orderedGroups) {
    group.sort((left, right) => left.sequenceNumber - right.sequenceNumber);
    const deduplicated = [...new Map(group.map((row) => [row.sequenceNumber, row])).values()];
    const first = deduplicated[0];
    const vertices = deduplicated.map<TrajectoryVertexDto>((row) => ({
      recordedAt: row.recordedAt,
      coordinates: [row.longitude, row.latitude],
      sourcePointId: row.sourcePointId,
      importanceMeters: row.importanceMeters,
      isInterpolated: false,
      anchorReasons: [...row.anchorReasons],
    }));
    const clipped = clipVertices(vertices, ranges[first.selectionRangeIndex]);
    if (clipped.length > 0) {
      metadata.push({ rangeIndex: first.selectionRangeIndex, segmentIndex: first.segmentIndex });
      clippedPaths.push(clipped);
    }
  }
  const simplified = simplifyPaths(
    clippedPaths,
    detail.pixelToleranceMeters,
    detail.vertexBudget,
  );
  return simplified.paths.map((vertices, index) => ({
    selectionRangeIndex: metadata[index].rangeIndex,
    segmentIndex: metadata[index].segmentIndex,
    selectedRange: ranges[metadata[index].rangeIndex],
    variantKind,
    isInferred: false,
    appliedImportanceThresholdMeters: simplified.threshold,
    vertices,
  }));
}
