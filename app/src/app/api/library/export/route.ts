import { parseCsvExportQuery } from "@/lib/csv-export";
import { getUserLibrary, LibraryRequestError, libraryErrorResponse } from "@/server/library-api";
import { originalCsvResponse, selectedCsvResponse } from "@/server/csv-exports";
import { requireViewAccount } from "@/server/workspace-views";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function download(request: Request) {
  try {
    if (request.url.length > 16000) throw new LibraryRequestError(414, "export_too_large", "导出选择过大，请减少时间区间。");
    const owner = await getUserLibrary(request);
    const url = new URL(request.url);
    // Native browser downloads cannot attach custom headers. The explicit,
    // non-secret page identity still prevents stale pages exporting a new login.
    const accountRequest = new Request(request.url, { headers: { "x-routefrom-account": url.searchParams.get("account") ?? "" } });
    requireViewAccount(owner, accountRequest);
    if (url.searchParams.has("file")) {
      if (url.searchParams.has("query")) throw new LibraryRequestError(400, "invalid_export", "请选择一种导出方式。");
      return await originalCsvResponse(owner, url.searchParams.get("file")!, request);
    }
    let query;
    try { query = parseCsvExportQuery(JSON.parse(url.searchParams.get("query") ?? "")); }
    catch { throw new LibraryRequestError(400, "invalid_export", "导出时间或轨迹选项无效。"); }
    return await selectedCsvResponse(owner, query, request);
  } catch (error) {
    const response = libraryErrorResponse(error);
    // Preflight reports validation without fetching the CSV into JS memory.
    if (request.method === "HEAD") response.headers.set("X-Routefrom-Export-Error", error instanceof LibraryRequestError ? error.code : "export_unavailable");
    return response;
  }
}
export const GET = download;
export const HEAD = download;
