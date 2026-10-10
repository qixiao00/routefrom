import "server-only";

import type { WorkspaceEvents } from "@/lib/workspace-data";
import type { TimeRange } from "@/lib/workspace-query";
import { getDatabase } from "./database";
import type { PublishedWorkspace } from "./workspace-access";

export async function readPersonalEvents(snapshot: PublishedWorkspace, ranges: readonly TimeRange[]): Promise<WorkspaceEvents> {
  const rows = await getDatabase()`
    WITH selected AS (
      SELECT tstzrange((value ->> 'start')::timestamptz, (value ->> 'end')::timestamptz, '[)') AS period
      FROM jsonb_array_elements(${JSON.stringify(ranges)}::jsonb)
    ), events AS (
      SELECT 'gaps' AS kind, lower(gap.gap_range) AS started, jsonb_build_object(
        'id', 'gap-' || gap.id, 'start', lower(gap.gap_range), 'end', upper(gap.gap_range), 'cause', gap.cause,
        'confidence', gap.confidence, 'reasonCodes', COALESCE(gap.evidence -> 'reason_codes', '[]'::jsonb),
        'samplingContext', gap.evidence -> 'sampling_context', 'normalWaitProbability', gap.evidence -> 'normal_wait_probability',
        'expectedIntervalSeconds', gap.evidence -> 'expected_interval_seconds',
        'startPosition', jsonb_build_array(ST_X(before.position), ST_Y(before.position)),
        'endPosition', jsonb_build_array(ST_X(after.position), ST_Y(after.position))) AS value
      FROM app.observation_gaps AS gap
      JOIN app.location_points AS before ON before.id = gap.before_point_id AND before.dataset_id = gap.dataset_id
      JOIN app.location_points AS after ON after.id = gap.after_point_id AND after.dataset_id = gap.dataset_id
      WHERE gap.processing_run_id = ${snapshot.processing_run_id}::uuid AND EXISTS (SELECT 1 FROM selected WHERE gap.gap_range && period)
      UNION ALL
      SELECT 'inferredConnections', lower(connection_range), jsonb_build_object(
        'id', id, 'gapId', 'gap-' || observation_gap_id, 'start', lower(connection_range), 'end', upper(connection_range),
        'kind', hypothesis_kind, 'confidence', confidence, 'displayable', displayable,
        'startPosition', jsonb_build_array(ST_X(start_position), ST_Y(start_position)),
        'endPosition', jsonb_build_array(ST_X(end_position), ST_Y(end_position)),
        'reasonCodes', COALESCE(evidence -> 'reason_codes', '[]'::jsonb))
      FROM app.inferred_connections WHERE processing_run_id = ${snapshot.processing_run_id}::uuid
        AND EXISTS (SELECT 1 FROM selected WHERE period @> connection_range)
      UNION ALL
      SELECT 'stays', lower(observed_range), jsonb_build_object(
        'id', id, 'start', lower(observed_range), 'end', upper(observed_range), 'kind', event_type,
        'position', jsonb_build_array(ST_X(centroid), ST_Y(centroid)), 'confidence', confidence,
        'visitProbability', visit_probability, 'durationSeconds', extract(epoch FROM upper(observed_range) - lower(observed_range)))
      FROM app.stationary_events WHERE processing_run_id = ${snapshot.processing_run_id}::uuid
        AND EXISTS (SELECT 1 FROM selected WHERE observed_range && period)
      UNION ALL
      SELECT 'trips', lower(trip_range), jsonb_build_object(
        'id', id, 'start', lower(trip_range), 'end', upper(trip_range), 'boundary', boundary_state, 'confidence', confidence,
        'distanceMeters', confirmed_distance_meters, 'unknownSeconds', unknown_duration_seconds)
      FROM app.trips WHERE processing_run_id = ${snapshot.processing_run_id}::uuid
        AND EXISTS (SELECT 1 FROM selected WHERE trip_range && period)
      UNION ALL
      SELECT 'modeLegs', lower(observed_range), jsonb_build_object(
        'id', id, 'start', lower(observed_range), 'end', upper(observed_range), 'mode', selected_mode_code,
        'level', selected_mode_level, 'confidence', confidence)
      FROM app.mobility_legs WHERE processing_run_id = ${snapshot.processing_run_id}::uuid
        AND EXISTS (SELECT 1 FROM selected WHERE observed_range && period)
    ) SELECT kind, jsonb_agg(value ORDER BY started, value ->> 'id') AS values FROM events GROUP BY kind
  `;
  const result: WorkspaceEvents = { gaps: [], inferredConnections: [], stays: [], trips: [], modeLegs: [] };
  for (const row of rows) {
    // PostgreSQL JSON timestamps include offsets; normalize before the existing
    // client compares ISO strings or applies discontinuous-slice boundaries.
    const entries = row.values.map((entry: { start: string; end: string }) => ({ ...entry,
      start: new Date(entry.start).toISOString(), end: new Date(entry.end).toISOString() }));
    Object.assign(result, { [row.kind]: entries });
  }
  return result;
}
