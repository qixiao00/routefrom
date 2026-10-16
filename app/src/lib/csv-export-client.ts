import type { CsvExportQuery } from "./csv-export.ts";

export function csvExportUrl(subject: string, selection: CsvExportQuery | { fileId: string }) {
  const params = new URLSearchParams({ account: subject });
  if ("fileId" in selection) params.set("file", selection.fileId);
  else params.set("query", JSON.stringify(selection));
  return `/api/library/export?${params}`;
}

export async function prepareCsvDownload(subject: string, selection: CsvExportQuery | { fileId: string }, signal?: AbortSignal) {
  const url = csvExportUrl(subject, selection);
  const response = await fetch(url, { method: "HEAD", cache: "no-store", signal: AbortSignal.any([...(signal ? [signal] : []), AbortSignal.timeout(45_000)]) });
  if (!response.ok) {
    const code = response.headers.get("x-routefrom-export-error");
    const message = response.status === 401 ? "登录已过期，请重新登录后下载。"
      : response.status === 404 ? "原始文件或足迹版本不存在，请刷新后重试。"
      : code === "account_changed" ? "账号已切换，请刷新页面后下载。"
      : code === "lineage_upgrade_required" ? "旧版本需要补充来源，请在地点备注面板重新处理足迹后导出。"
      : code === "variant_unavailable" ? "当前版本没有所选轨迹，请改用去噪 GPS。"
      : "暂时无法下载，请检查连接及私有存储后重试。";
    throw new Error(message);
  }
  return url;
}

export function startCsvDownload(url: string) {
  // Native download streams to disk. Never accumulate 80 MB originals or an
  // entire history in a fetch Blob, React state or the browser's JS heap.
  const link = document.createElement("a");
  link.href = url; link.download = ""; link.target = "_blank"; link.rel = "noopener noreferrer";
  document.body.append(link); link.click(); link.remove();
}
