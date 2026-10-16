import { parseWorkspaceQuery, type TimeRange } from "./workspace-query.ts";

export const OBSERVATION_COLUMNS = ["geoTime", "latitude", "longitude", "wgsLatitude", "wgsLongitude", "altitude", "course",
  "horizontalAccuracy", "verticalAccuracy", "speed", "dayTimeMills", "networkType", "networkName", "locationType"];
export const OBSERVATION_FIELDS = ["geo_time_epoch_ms", "source_latitude", "source_longitude", "wgs_latitude", "wgs_longitude",
  "source_altitude", "source_course", "source_horizontal_accuracy", "source_vertical_accuracy", "source_speed",
  "day_time_epoch_ms", "network_type", "network_name", "location_type"];
export const TRAJECTORY_COLUMNS = ["recordedAt", "longitudeWgs84", "latitudeWgs84", "selectionRangeIndex", "segmentIndex", "segmentPointIndex",
  "sourceObservationId", "sourceFingerprint", "sourceId", "qualityStatus", "anomalyProbability", "reasonCodes",
  "variant", "processingRunId", "algorithmVersion"];
export type CsvExportKind = "observations" | "cleaned_gps" | "smoothed_gps";
export interface CsvExportQuery {
  datasetId: string;
  processingRunId: string;
  kind: CsvExportKind;
  timeSelection: { timezone: string; ranges: TimeRange[] };
}

export function parseCsvExportQuery(value: unknown): CsvExportQuery {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("invalid export");
  const body = value as Record<string, unknown>;
  if (Object.keys(body).some(key => !["datasetId", "processingRunId", "kind", "timeSelection"].includes(key))) throw new Error("unknown export option");
  if (typeof body.kind !== "string" || !["observations", "cleaned_gps", "smoothed_gps"].includes(body.kind)) throw new Error("invalid export kind");
  const parsed = parseWorkspaceQuery({ datasetId: body.datasetId, processingRunId: body.processingRunId, timeSelection: body.timeSelection });
  // Bind downloads to the version actually viewed, never silently export a newer run.
  if (parsed.processingRunId === "active") throw new Error("export requires an exact run");
  return { datasetId: parsed.datasetId, processingRunId: parsed.processingRunId, kind: body.kind as CsvExportKind, timeSelection: parsed.timeSelection };
}

export function csvRow(values: readonly (string | number | null | undefined)[]): string {
  return values.map(value => {
    const text = value === null || value === undefined ? "" : String(value);
    return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
  }).join(",") + "\r\n";
}

export function csvDownloadName(name: string): string {
  // RFC 5987 plus a fixed ASCII fallback; no raw user text in HTTP headers.
  const safe = Array.from(name.replace(/[\x00-\x1f\x7f/\\]/g, "_")).slice(0, 180).join("") || "routefrom.csv";
  const encoded = encodeURIComponent(safe).replace(/[!'()*]/g, char => `%${char.charCodeAt(0).toString(16).toUpperCase()}`);
  return `attachment; filename="routefrom.csv"; filename*=UTF-8''${encoded}`;
}
