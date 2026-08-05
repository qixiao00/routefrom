import "server-only";

export class WorkspaceApiDisabledError extends Error {}
export class DatasetNotFoundError extends Error {}

export function requireDatasetAccess(datasetId: string): void {
  const enabled = process.env.ROUTEFROM_ENABLE_LOCAL_DATA_API === "true";
  const allowedDatasetId = process.env.ROUTEFROM_ALLOWED_DATASET_ID?.toLowerCase();
  if (!enabled || !allowedDatasetId) {
    throw new WorkspaceApiDisabledError("local workspace data API is disabled");
  }
  if (datasetId !== allowedDatasetId) {
    throw new DatasetNotFoundError("dataset was not found");
  }
}
