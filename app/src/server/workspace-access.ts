import "server-only";

import { getDatabase } from "./database";
import { getUserLibrary, LibraryRequestError } from "./library-api";

export type UserLibrary = Awaited<ReturnType<typeof getUserLibrary>>;
export interface PublishedWorkspace {
  id: string;
  dataset_id: string;
  processing_run_id: string;
  point_count: string | number;
  published_at: string;
  recorded_from: string;
  recorded_to: string;
  algorithm_version: string;
  metrics: { row_counts?: Record<string, number> };
}

const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;

export async function resolvePublishedWorkspace(
  owner: UserLibrary,
  requested?: { datasetId: string; processingRunId: string },
): Promise<PublishedWorkspace | null> {
  if (requested && (!UUID.test(requested.datasetId) ||
    (requested.processingRunId !== "active" && !UUID.test(requested.processingRunId)))) {
    throw new LibraryRequestError(404, "not_found", "足迹版本不存在。");
  }
  const rows = await getDatabase()`
    SELECT snapshot.id, snapshot.dataset_id, snapshot.processing_run_id,
           snapshot.point_count, snapshot.published_at, dataset.recorded_from, dataset.recorded_to,
           run.algorithm_version, run.metrics
    FROM app.library_snapshots AS snapshot
    LEFT JOIN app.datasets AS dataset ON dataset.id = snapshot.dataset_id AND dataset.user_id = snapshot.user_id
    LEFT JOIN app.processing_runs AS run ON run.id = snapshot.processing_run_id AND run.dataset_id = snapshot.dataset_id
    WHERE snapshot.library_id = ${owner.library.id}::uuid AND snapshot.user_id = ${owner.user.id}::uuid
      AND snapshot.status IN ('active', 'superseded') AND snapshot.published_at IS NOT NULL
      AND CASE WHEN ${requested !== undefined} THEN
        snapshot.dataset_id = ${requested?.datasetId ?? null}::uuid AND
        CASE WHEN ${requested?.processingRunId === "active"} THEN snapshot.id = ${owner.library.active_snapshot_id}::uuid
             ELSE snapshot.processing_run_id = ${requested?.processingRunId === "active" ? null : requested?.processingRunId ?? null}::uuid END
      ELSE snapshot.id = ${owner.library.active_snapshot_id}::uuid END
    LIMIT 1
  ` as PublishedWorkspace[];
  if (requested && (!rows.length || !rows[0].processing_run_id)) {
    throw new LibraryRequestError(404, "not_found", "足迹版本不存在。");
  }
  return rows[0] ?? null;
}
