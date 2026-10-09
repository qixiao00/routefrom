export interface LibraryConflict {
  epochMs: number;
  candidates: Array<{ fingerprint: string; sourceId: string; longitude: number; latitude: number; accuracyMeters: number | null }>;
}

export interface LibraryFile {
  id: string;
  dataset_id: string;
  name: string;
  point_count: number;
  source_size_bytes: number | string;
  status: "active" | "withdrawn";
  added_at: string;
  recorded_from: string | null;
  recorded_to: string | null;
}

export interface LibraryJob {
  id: string;
  dataset_id: string | null;
  kind: "import" | "reprocess";
  status: "queued" | "running" | "awaiting_resolution" | "succeeded" | "failed" | "superseded";
  stage: string;
  progress: number;
  name: string | null;
  created_at: string;
  error_message: string | null;
  metrics: { point_count?: number; source_rows?: number; added_observations?: number; duplicate_rows?: number;
    remaining_conflict_count?: number; conflicts?: LibraryConflict[] };
}

export interface LibraryOverview {
  user: { id: string; name: string; timezone: string };
  library: { id: string; name: string; revision: string | number; active_snapshot_id: string | null };
  published: { revision: number | string; point_count: number | string; published_at: string } | null;
  files: LibraryFile[];
  jobs: LibraryJob[];
}

export class LibraryApiError extends Error {
  readonly status: number;
  readonly code: string;
  constructor(message: string, status: number, code: string) { super(message); this.status = status; this.code = code; }
}

async function readResponse(response: Response) {
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new LibraryApiError(body?.error?.message ?? "暂时无法处理足迹，请稍后重试。",
    response.status, body?.error?.code ?? "library_unavailable");
  if (!body || typeof body !== "object") throw new LibraryApiError("服务器返回内容不完整，请刷新。", 502, "invalid_response");
  return body;
}

export async function loadLibrary(signal?: AbortSignal): Promise<LibraryOverview> {
  return readResponse(await fetch("/api/library", { cache: "no-store", signal }));
}

export async function uploadLibraryFile(file: File, signal?: AbortSignal): Promise<{ reused: boolean; fileId?: string; job?: { id: string; status: string } }> {
  if (!file.name.toLowerCase().endsWith(".csv") || !file.size) throw new LibraryApiError("请选择非空的 CSV 文件。", 400, "invalid_csv");
  if (file.size > 80 * 1024 * 1024) throw new LibraryApiError(`${file.name} 超过 80 MB，请拆分后上传。`, 413, "upload_too_large");
  const form = new FormData();
  form.append("file", file);
  return readResponse(await fetch("/api/library/imports", { method: "POST", cache: "no-store", body: form, signal }));
}

export async function changeLibraryFile(id: string, action: "withdraw" | "restore") {
  return readResponse(await fetch(`/api/library/files/${encodeURIComponent(id)}`, { method: "POST", cache: "no-store",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action }) }));
}

export async function updateLibraryJob(id: string, action: "retry" | "resolve", resolutions?: Record<string, string>) {
  return readResponse(await fetch(`/api/library/jobs/${encodeURIComponent(id)}`, { method: "POST", cache: "no-store",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action, ...(resolutions ? { resolutions } : {}) }) }));
}

export function jobStatusLabel(status: LibraryJob["status"]) {
  return { queued: "等待处理", running: "正在处理", awaiting_resolution: "需要选择", succeeded: "处理完成", failed: "处理失败", superseded: "已被更新取代" }[status];
}

export function jobStageLabel(stage: string) {
  return { queued: "等待后台处理", validate_source: "检查原始 CSV", attach_source: "加入个人足迹库", merge_library: "合并与精确去重",
    resolve_conflicts: "等待冲突选择", run_algorithms: "识别足迹、停留与行程", persist_results: "保存新版本", publish: "发布新版本",
    complete: "新结果已发布", failed: "本次处理未完成", withdrawn: "文件已撤销" }[stage] ?? "处理足迹";
}

export function validConflictDecisions(conflicts: readonly LibraryConflict[], selections: Record<string, string>) {
  const accepted: Record<string, string> = {};
  for (const conflict of conflicts) {
    const chosen = selections[String(conflict.epochMs)];
    if (conflict.candidates.some(candidate => candidate.fingerprint === chosen)) accepted[String(conflict.epochMs)] = chosen;
  }
  return accepted;
}

export function fileCoverage(files: readonly LibraryFile[], file: LibraryFile): { left: number; width: number } | null {
  const valid = files.filter(item => item.recorded_from && item.recorded_to && Number.isFinite(Date.parse(item.recorded_from)) && Number.isFinite(Date.parse(item.recorded_to)));
  if (!file.recorded_from || !file.recorded_to || !valid.length) return null;
  const start = Date.parse(file.recorded_from);
  const end = Date.parse(file.recorded_to);
  if (!Number.isFinite(start) || !Number.isFinite(end) || start > end) return null;
  const first = Math.min(...valid.map(item => Date.parse(item.recorded_from!)));
  const last = Math.max(...valid.map(item => Date.parse(item.recorded_to!)));
  if (last <= first) return { left: 0, width: 100 };
  const left = Math.max(0, Math.min(99, (start - first) / (last - first) * 100));
  return { left, width: Math.min(100 - left, Math.max(1, (end - start) / (last - first) * 100)) };
}

export function loginReturnPath(value: string | null): "/" | "/library" {
  return value === "/library" ? "/library" : "/";
}
