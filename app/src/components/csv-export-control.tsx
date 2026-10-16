"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import * as Popover from "@radix-ui/react-popover";
import { Download, LoaderCircle } from "lucide-react";
import type { CsvExportKind, CsvExportQuery } from "@/lib/csv-export";
import { prepareCsvDownload, startCsvDownload } from "@/lib/csv-export-client";

export function CsvExportControl({ subject, selection }: { subject: string; selection: Omit<CsvExportQuery, "kind"> | null }) {
  const [kind, setKind] = useState<CsvExportKind>("observations");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  return <Popover.Root><Popover.Trigger asChild><button className="map-view-sync" type="button" aria-label="导出足迹 CSV" disabled={!selection?.timeSelection.ranges.length}>
    <Download size={14} /><span>导出</span></button></Popover.Trigger>
    <Popover.Portal><Popover.Content className="map-views-popover csv-export-panel" align="end" sideOffset={12} collisionPadding={12}>
      <div className="map-views-heading"><div><small>把选择的足迹带走</small><h2>导出 CSV</h2></div><Download size={20} /></div>
      <p className="map-views-hint">当前 {selection?.timeSelection.ranges.length ?? 0} 个时间区间，不受地图视野或缩放影响。起点包含、终点不包含，重叠区间不会重复导出。</p>
      <fieldset className="csv-export-options" disabled={busy}>
        <legend>导出内容</legend>
        {([
          ["observations", "合并后的源观测", "精确去重及冲突选择后的灵敢格式，保留源数值和异常点，可重新导入。"],
          ["cleaned_gps", "去噪 GPS 轨迹", "排除异常点，保留疑似点及质量说明；独立格式，用段编号标明断点。"],
          ["smoothed_gps", "平滑 GPS 轨迹", "使用处理后保存的平滑位置，附质量和来源；不是原始记录。"],
        ] as const).map(([value, title, hint]) => <label key={value}><input type="radio" name="csv-export-kind" value={value} checked={kind === value} onChange={() => setKind(value)} />
          <span><strong>{title}</strong><small>{hint}</small></span></label>)}
      </fieldset>
      <p className="map-views-hint">不含推测线、插值或显示聚合。轨迹请按时间片编号与段编号分别连线。源 CSV 保留外部文本，请勿在表格软件中启用其公式。</p>
      {error && <p className="map-views-operation-error" role="alert">{error}</p>}
      {notice && <p className="map-views-hint" role="status">{notice}</p>}
      <button className="csv-export-download" type="button" disabled={busy || !selection?.timeSelection.ranges.length} onClick={() => {
        if (!selection || controller.current) return;
        const abort = new AbortController(); controller.current = abort; setBusy(true); setError(null); setNotice(null);
        void prepareCsvDownload(subject, { ...selection, kind }, abort.signal).then(url => {
          if (!abort.signal.aborted) { startCsvDownload(url); setNotice("已交给浏览器下载，请查看下载列表。大文件会持续写入，不影响地图浏览。"); }
        }).catch(cause => { if (!abort.signal.aborted) setError(cause instanceof Error ? cause.message : "下载失败。"); })
          .finally(() => { controller.current = null; if (!abort.signal.aborted) setBusy(false); });
      }}>{busy ? <LoaderCircle size={14} className="is-spinning" /> : <Download size={14} />}{busy ? "检查导出数据…" : "下载当前时间选择"}</button>
      <Link className="place-notes-library-link" href="/library">前往足迹库下载原始文件</Link>
      <Popover.Arrow className="map-views-arrow" />
    </Popover.Content></Popover.Portal>
  </Popover.Root>;
}
