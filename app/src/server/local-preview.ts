import "server-only";

import { readFile } from "node:fs/promises";
import path from "node:path";

import type { WorkspacePreview } from "@/lib/workspace-data";

export class LocalPreviewNotFoundError extends Error {}
export class LocalPreviewInvalidError extends Error {}

const MAX_PREVIEW_BYTES = 12 * 1024 * 1024;

export async function readLocalWorkspacePreview(): Promise<WorkspacePreview> {
  const configuredPath = process.env.ROUTEFROM_LOCAL_PREVIEW_PATH;
  const previewPath = configuredPath
    ? path.resolve(configuredPath)
    : path.resolve(process.cwd(), "..", "data", "generated", "workspace-preview.json");
  let content: string;
  try {
    content = await readFile(previewPath, "utf8");
  } catch (error) {
    throw new LocalPreviewNotFoundError(`local preview was not found at ${previewPath}`, {
      cause: error,
    });
  }
  if (Buffer.byteLength(content, "utf8") > MAX_PREVIEW_BYTES) {
    throw new LocalPreviewInvalidError("local preview exceeds 12 MiB");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(content);
  } catch (error) {
    throw new LocalPreviewInvalidError("local preview is not valid JSON", { cause: error });
  }
  if (
    typeof parsed !== "object" ||
    parsed === null ||
    !("schemaVersion" in parsed) ||
    parsed.schemaVersion !== 1 ||
    !("paths" in parsed) ||
    !Array.isArray(parsed.paths)
  ) {
    throw new LocalPreviewInvalidError("local preview schema is not supported");
  }
  return parsed as WorkspacePreview;
}
