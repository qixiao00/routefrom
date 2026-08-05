import "server-only";

import {
  buildTrajectoryPaths,
  type StoredTrajectoryVertex,
  type TrajectoryPathDto,
  type WorkspaceQuery,
} from "@/lib/workspace-query";
import { getDatabase } from "@/server/database";

interface TrajectoryRow {
  processing_run_id: string;
  variant_kind: TrajectoryPathDto["variantKind"] | null;
  selection_range_index: number | string | null;
  segment_index: number | string | null;
  sequence_number: number | string | null;
  recorded_at: Date | string | null;
  longitude: number | string | null;
  latitude: number | string | null;
  source_point_id: bigint | number | string | null;
  importance_meters: number | string | null;
  anchor_reasons: string[] | null;
}

export class ProcessingRunNotFoundError extends Error {}
export class TrajectoryVariantNotFoundError extends Error {}

export interface WorkspaceTrajectoryResult {
  datasetId: string;
  processingRunId: string;
  normalizedRanges: WorkspaceQuery["timeSelection"]["ranges"];
  trajectoryPaths: TrajectoryPathDto[];
}

function asNumber(value: number | string | null, field: string): number {
  const parsed = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(parsed)) throw new Error(`database returned invalid ${field}`);
  return parsed;
}

export async function queryWorkspaceTrajectory(
  query: WorkspaceQuery,
): Promise<WorkspaceTrajectoryResult> {
  const sql = getDatabase();
  const rangeJson = JSON.stringify(query.timeSelection.ranges);
  const rows = await sql`
    WITH requested_run AS (
      SELECT run.id
      FROM app.processing_runs AS run
      WHERE run.dataset_id = ${query.datasetId}::uuid
        AND run.status = 'succeeded'
        AND CASE
          WHEN ${query.processingRunId} = 'active' THEN run.publication_status = 'active'
          ELSE run.id = NULLIF(${query.processingRunId}, 'active')::uuid
        END
      LIMIT 1
    ),
    selected_variant AS (
      SELECT variant.id, variant.variant_kind
      FROM app.trajectory_variants AS variant
      JOIN requested_run AS run ON run.id = variant.processing_run_id
      ORDER BY variant.preferred_for_display DESC, variant.created_at DESC
      LIMIT 1
    ),
    selected_ranges AS (
      SELECT
        range.ordinality::integer - 1 AS selection_range_index,
        tstzrange(
          (range.value ->> 'start')::timestamptz,
          (range.value ->> 'end')::timestamptz,
          '[)'
        ) AS selected_range
      FROM jsonb_array_elements(${rangeJson}::jsonb) WITH ORDINALITY AS range(value, ordinality)
    ),
    vertex_windows AS (
      SELECT
        selected.selection_range_index,
        chunk.trajectory_segment_id,
        min(chunk.first_vertex_sequence) AS first_sequence,
        max(chunk.last_vertex_sequence) AS last_sequence
      FROM selected_ranges AS selected
      CROSS JOIN selected_variant AS variant
      JOIN app.trajectory_chunks AS chunk
        ON chunk.trajectory_variant_id = variant.id
       AND chunk.time_range && selected.selected_range
      GROUP BY selected.selection_range_index, chunk.trajectory_segment_id
    ),
    selected_vertices AS (
      SELECT
        vertex_window.selection_range_index,
        segment.sequence_number AS segment_index,
        vertex.sequence_number,
        vertex.recorded_at,
        ST_X(vertex.position) AS longitude,
        ST_Y(vertex.position) AS latitude,
        vertex.source_point_id,
        vertex.importance_meters,
        vertex.anchor_reasons
      FROM vertex_windows AS vertex_window
      JOIN app.trajectory_segments AS segment
        ON segment.id = vertex_window.trajectory_segment_id
      JOIN app.trajectory_vertices AS vertex
        ON vertex.trajectory_segment_id = vertex_window.trajectory_segment_id
       AND vertex.sequence_number
           BETWEEN vertex_window.first_sequence AND vertex_window.last_sequence
    )
    SELECT
      run.id::text AS processing_run_id,
      variant.variant_kind,
      vertex.selection_range_index,
      vertex.segment_index,
      vertex.sequence_number,
      vertex.recorded_at,
      vertex.longitude,
      vertex.latitude,
      vertex.source_point_id,
      vertex.importance_meters,
      vertex.anchor_reasons
    FROM requested_run AS run
    LEFT JOIN selected_variant AS variant ON true
    LEFT JOIN selected_vertices AS vertex ON true
    ORDER BY vertex.selection_range_index, vertex.segment_index, vertex.sequence_number
  ` as TrajectoryRow[];

  if (rows.length === 0) throw new ProcessingRunNotFoundError("processing run was not found");
  if (rows[0].variant_kind === null) {
    throw new TrajectoryVariantNotFoundError("processing run has no trajectory variant");
  }
  const vertices: StoredTrajectoryVertex[] = rows
    .filter((row) => row.sequence_number !== null)
    .map((row) => ({
      selectionRangeIndex: asNumber(row.selection_range_index, "selection range index"),
      segmentIndex: asNumber(row.segment_index, "segment index"),
      sequenceNumber: asNumber(row.sequence_number, "sequence number"),
      recordedAt: new Date(row.recorded_at!).toISOString(),
      longitude: asNumber(row.longitude, "longitude"),
      latitude: asNumber(row.latitude, "latitude"),
      sourcePointId: row.source_point_id === null ? null : String(row.source_point_id),
      importanceMeters:
        row.importance_meters === null ? null : asNumber(row.importance_meters, "importance"),
      anchorReasons: row.anchor_reasons ?? [],
    }));
  return {
    datasetId: query.datasetId,
    processingRunId: rows[0].processing_run_id,
    normalizedRanges: query.timeSelection.ranges,
    trajectoryPaths: buildTrajectoryPaths(
      vertices,
      query.timeSelection.ranges,
      rows[0].variant_kind,
      query.trajectoryDetail,
    ),
  };
}
