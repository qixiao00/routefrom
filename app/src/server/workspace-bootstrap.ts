import "server-only";

import type { PreviewPlace, WorkspacePreview } from "@/lib/workspace-data";
import { getDatabase } from "./database";
import { resolvePublishedWorkspace, type UserLibrary } from "./workspace-access";

const instant = (value: string | Date) => new Date(value).toISOString();

export async function readPersonalWorkspace(owner: UserLibrary): Promise<WorkspacePreview> {
  const snapshot = await resolvePublishedWorkspace(owner);
  const now = new Date().toISOString();
  const base: WorkspacePreview = {
    schemaVersion: 1,
    account: owner.user,
    library: { id: owner.library.id, state: snapshot?.processing_run_id ? "ready" : "empty", publishedAt: snapshot?.published_at ?? null },
    dataset: { id: snapshot?.dataset_id ?? owner.library.id, name: owner.library.name, sourceName: "个人足迹库",
      pointCount: Number(snapshot?.point_count ?? 0), startedAt: now, endedAt: now },
    processing: { runId: snapshot?.processing_run_id ?? owner.library.id, algorithmVersion: snapshot?.algorithm_version ?? "—",
      trajectoryVariant: "cleaned_gps", smoothingConfidence: 0 },
    summary: { gapCount: 0, stayCount: 0, tripCount: 0, placeCount: 0, modeLegCount: 0 },
    suggestedRanges: [], suggestedMapView: { longitude: 105, latitude: 28, zoom: 1.5, pitch: 0, bearing: 0 },
    paths: [], gaps: [], inferredConnections: [], stays: [], trips: [], modeLegs: [], places: [],
  };
  if (!snapshot?.processing_run_id) return base;
  const sql = getDatabase();
  const [variants, places] = await Promise.all([
    sql`
      SELECT variant.variant_kind, variant.confidence,
        (SELECT ST_X(position) FROM app.trajectory_vertices WHERE trajectory_variant_id = variant.id
         ORDER BY sequence_number DESC LIMIT 1) AS longitude,
        (SELECT ST_Y(position) FROM app.trajectory_vertices WHERE trajectory_variant_id = variant.id
         ORDER BY sequence_number DESC LIMIT 1) AS latitude
      FROM app.trajectory_variants AS variant WHERE variant.processing_run_id = ${snapshot.processing_run_id}::uuid
      ORDER BY preferred_for_display DESC, created_at DESC LIMIT 1
    `,
    sql`
      SELECT version.place_id AS id, COALESCE(version.name, place.name) AS name,
             ST_X(version.centroid) AS longitude, ST_Y(version.centroid) AS latitude, version.confidence, version.evidence
      FROM app.place_versions AS version JOIN app.places AS place ON place.id = version.place_id
      WHERE version.processing_run_id = ${snapshot.processing_run_id}::uuid AND place.user_id = ${owner.user.id}::uuid
      ORDER BY version.place_id
    `,
  ]);
  const counts = snapshot.metrics?.row_counts ?? {};
  const start = instant(snapshot.recorded_from);
  const end = new Date(Date.parse(instant(snapshot.recorded_to)) + 1).toISOString();
  base.dataset.startedAt = start; base.dataset.endedAt = end;
  base.suggestedRanges = [{ start: new Date(Math.max(Date.parse(start), Date.parse(end) - 14 * 86400000)).toISOString(), end }];
  base.summary = { gapCount: counts.observation_gaps ?? 0, stayCount: counts.stationary_events ?? 0,
    tripCount: counts.trips ?? 0, placeCount: counts.place_versions ?? 0, modeLegCount: counts.mobility_legs ?? 0 };
  if (variants[0]) {
    base.processing.trajectoryVariant = variants[0].variant_kind;
    base.processing.smoothingConfidence = Number(variants[0].confidence ?? 0);
    if (variants[0].longitude !== null && variants[0].latitude !== null) {
      base.suggestedMapView = { longitude: Number(variants[0].longitude), latitude: Number(variants[0].latitude), zoom: 10, pitch: 0, bearing: 0 };
    }
  }
  base.places = places.map((row): PreviewPlace => ({ id: row.id, name: row.name ?? "推测地点",
    position: [Number(row.longitude), Number(row.latitude)], confidence: Number(row.confidence ?? 0),
    visitCount: Number(row.evidence.visit_count ?? 0), dwellSeconds: Number(row.evidence.observed_duration_seconds ?? 0),
    frequentProbability: Number(row.evidence.frequent_probability ?? 0),
    firstVisitedAt: instant(row.evidence.first_visited_at), lastVisitedAt: instant(row.evidence.last_visited_at) }));
  return base;
}
