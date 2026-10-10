import "server-only";

import type { PreviewPath, PreviewVertex } from "@/lib/workspace-data";
import { aggregateOrdinaryRoutes } from "@/lib/map-route-aggregation";
import { buildViewportSelection, queryViewportSelection, unionBounds, type MovementClass, type ViewportBounds,
  type ViewportRequest, type ViewportResponse } from "@/lib/workspace-viewport";
import { getDatabase } from "./database";
import type { PublishedWorkspace } from "./workspace-access";

interface SelectionSummaryRow { movement_class: MovementClass; meters: number | string;
  west: number | string; south: number | string; east: number | string; north: number | string }
const summaryCache = new Map<string, Promise<SelectionSummaryRow[]>>();
function cachedSummary(key: string, query: () => Promise<SelectionSummaryRow[]>) {
  const cached = summaryCache.get(key);
  if (cached) return cached;
  const pending = query().catch(error => {
    if (summaryCache.get(key) === pending) summaryCache.delete(key);
    throw error;
  });
  summaryCache.set(key, pending);
  if (summaryCache.size > 32) summaryCache.delete(summaryCache.keys().next().value!);
  return pending;
}

export async function readPersonalViewport(snapshot: PublishedWorkspace, query: ViewportRequest): Promise<ViewportResponse> {
  const sql = getDatabase();
  const variants = await sql`SELECT id FROM app.trajectory_variants WHERE processing_run_id = ${snapshot.processing_run_id}::uuid
    ORDER BY preferred_for_display DESC, created_at DESC LIMIT 1`;
  const variantId = variants[0]?.id;
  if (!variantId) return queryViewportSelection(buildViewportSelection([], query.ranges), query.bounds, query.detailZoom);
  const ranges = JSON.stringify(query.ranges);
  const [vertices, airLegs, summary] = await Promise.all([
    sql`
      WITH selected AS (
        SELECT tstzrange((value ->> 'start')::timestamptz, (value ->> 'end')::timestamptz, '[)') AS period
        FROM jsonb_array_elements(${ranges}::jsonb)
      ), chunks AS (
        SELECT trajectory_segment_id, first_vertex_sequence, last_vertex_sequence,
          max(last_vertex_sequence) OVER (PARTITION BY trajectory_segment_id ORDER BY first_vertex_sequence
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS previous_last
        FROM app.trajectory_chunks WHERE trajectory_variant_id = ${variantId}::uuid
          AND bounds && ST_MakeEnvelope(${query.bounds[0]}, ${query.bounds[1]}, ${query.bounds[2]}, ${query.bounds[3]}, 4326)
          AND EXISTS (SELECT 1 FROM selected WHERE time_range && period)
      ), marked AS (
        SELECT *, sum(CASE WHEN previous_last IS NULL OR first_vertex_sequence > previous_last + 1 THEN 1 ELSE 0 END)
          OVER (PARTITION BY trajectory_segment_id ORDER BY first_vertex_sequence) AS island FROM chunks
      ), windows AS (
        SELECT trajectory_segment_id, island, min(first_vertex_sequence) AS first_sequence,
          max(last_vertex_sequence) AS last_sequence FROM marked GROUP BY trajectory_segment_id, island
      )
      SELECT span.trajectory_segment_id AS segment_id, span.island, vertex.sequence_number,
        vertex.recorded_at, ST_X(vertex.position) AS longitude, ST_Y(vertex.position) AS latitude, vertex.importance_meters
      FROM windows AS span JOIN app.trajectory_vertices AS vertex
        ON vertex.trajectory_segment_id = span.trajectory_segment_id
        AND vertex.trajectory_variant_id = ${variantId}::uuid
        AND vertex.sequence_number BETWEEN span.first_sequence AND span.last_sequence
      ORDER BY span.trajectory_segment_id, span.island, vertex.sequence_number
    `,
    sql`SELECT id, lower(observed_range) AS start, upper(observed_range) AS end, confidence
      FROM app.mobility_legs WHERE processing_run_id = ${snapshot.processing_run_id}::uuid AND selected_mode_code = 'air'`,
    cachedSummary(`${snapshot.id}:${variantId}:${ranges}`, async () => await sql`
      WITH selected AS (
        SELECT (value ->> 'start')::timestamptz AS started_at, (value ->> 'end')::timestamptz AS ended_at
        FROM jsonb_array_elements(${ranges}::jsonb)
      ), windows AS (
        SELECT selected.started_at, selected.ended_at, chunk.trajectory_segment_id,
          min(chunk.first_vertex_sequence) AS first_sequence, max(chunk.last_vertex_sequence) AS last_sequence
        FROM selected JOIN app.trajectory_chunks AS chunk ON chunk.trajectory_variant_id = ${variantId}::uuid
          AND chunk.time_range && tstzrange(selected.started_at, selected.ended_at, '[)')
        GROUP BY selected.started_at, selected.ended_at, chunk.trajectory_segment_id
      ), edges AS (
        SELECT greatest(left_vertex.recorded_at, selected.started_at) AS started_at,
          least(right_vertex.recorded_at, selected.ended_at) AS ended_at,
          ST_X(left_vertex.position) AS ax, ST_Y(left_vertex.position) AS ay,
          ST_X(right_vertex.position) - ST_X(left_vertex.position) AS dx,
          ST_Y(right_vertex.position) - ST_Y(left_vertex.position) AS dy,
          extract(epoch FROM greatest(left_vertex.recorded_at, selected.started_at) - left_vertex.recorded_at) /
            extract(epoch FROM right_vertex.recorded_at - left_vertex.recorded_at) AS f0,
          extract(epoch FROM least(right_vertex.recorded_at, selected.ended_at) - left_vertex.recorded_at) /
            extract(epoch FROM right_vertex.recorded_at - left_vertex.recorded_at) AS f1
        FROM windows AS selected JOIN app.trajectory_vertices AS left_vertex
          ON left_vertex.trajectory_variant_id = ${variantId}::uuid AND left_vertex.trajectory_segment_id = selected.trajectory_segment_id
          AND left_vertex.sequence_number BETWEEN selected.first_sequence AND selected.last_sequence
          AND left_vertex.recorded_at < selected.ended_at
        JOIN app.trajectory_vertices AS right_vertex ON right_vertex.trajectory_segment_id = left_vertex.trajectory_segment_id
          AND right_vertex.segment_sequence_number = left_vertex.segment_sequence_number + 1
          AND right_vertex.recorded_at >= selected.started_at AND right_vertex.recorded_at > left_vertex.recorded_at
      ), clipped AS (
        SELECT started_at, ended_at, ax + dx * f0 AS x0, ay + dy * f0 AS y0, ax + dx * f1 AS x1, ay + dy * f1 AS y1
        FROM edges WHERE started_at < ended_at
      ), measured AS (
        SELECT *, 2 * 6371008.8 * asin(least(1.0, sqrt(
          power(sin(radians(y1 - y0) / 2), 2) + cos(radians(y0)) * cos(radians(y1)) * power(sin(radians(x1 - x0) / 2), 2)
        ))) AS meters FROM clipped
      ), classified AS (
        SELECT *, CASE WHEN meters / extract(epoch FROM ended_at - started_at) >= 70 OR EXISTS (
          SELECT 1 FROM app.mobility_legs WHERE processing_run_id = ${snapshot.processing_run_id}::uuid
            AND selected_mode_code = 'air' AND observed_range @> (started_at + (ended_at - started_at) / 2)
        ) THEN 'high_speed' WHEN meters >= 500 THEN 'sparse' ELSE 'ordinary' END AS movement_class
        FROM measured
      ) SELECT movement_class, sum(meters) AS meters, least(min(x0), min(x1)) AS west,
          least(min(y0), min(y1)) AS south, greatest(max(x0), max(x1)) AS east, greatest(max(y0), max(y1)) AS north
        FROM classified GROUP BY movement_class
    ` as SelectionSummaryRow[]),
  ]);
  // Spatially separated windows must be separate paths even when they belong to
  // one original segment. Never bridge chunks that were not loaded into view.
  const paths: PreviewPath[] = [];
  let key = "";
  for (const row of vertices) {
    const nextKey = `${row.segment_id}:${row.island}`;
    if (key !== nextKey) { paths.push({ segmentIndex: paths.length, vertices: [] }); key = nextKey; }
    const vertex: PreviewVertex = [new Date(row.recorded_at).toISOString(), Number(row.longitude), Number(row.latitude),
      row.importance_meters === null ? null : Number(row.importance_meters)];
    paths.at(-1)!.vertices.push(vertex);
  }
  const legs = airLegs.map(row => ({ id: row.id, start: new Date(row.start).toISOString(), end: new Date(row.end).toISOString(),
    mode: "air", level: "specific", confidence: Number(row.confidence) }));
  const result = queryViewportSelection(buildViewportSelection(paths, query.ranges, legs), query.bounds, query.detailZoom, 25000);
  result.selectedDistanceMeters = 0;
  result.selectedBounds = null;
  result.selectedBoundsByClass = { ordinary: null, sparse: null, high_speed: null };
  for (const row of summary) {
    const bounds: ViewportBounds = [Number(row.west), Number(row.south), Number(row.east), Number(row.north)];
    result.selectedDistanceMeters += Number(row.meters);
    result.selectedBoundsByClass[row.movement_class as MovementClass] = bounds;
    result.selectedBounds = unionBounds(result.selectedBounds, bounds);
  }
  result.aggregatedRoutes = aggregateOrdinaryRoutes(result.paths, query.detailZoom);
  return result;
}
