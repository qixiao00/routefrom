"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, ArrowLeft, ArrowUpRight, Check, FileText, LoaderCircle, LogOut, RefreshCw, Route, Upload, Undo2 } from "lucide-react";
import { authClient } from "@/lib/auth-client";
import { changeLibraryFile, fileCoverage, jobStageLabel, jobStatusLabel, LibraryApiError, loadLibrary, updateLibraryJob,
  uploadLibraryFile, validConflictDecisions, type LibraryJob, type LibraryOverview } from "@/lib/library-client";

function instant(value: string | null, time = false, seconds = false) {
  return value ? new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit",
    ...(time ? { hour: "2-digit", minute: "2-digit", ...(seconds ? { second: "2-digit" } : {}), hour12: false } : {}) }).format(new Date(value)) : "—";
}
const count = (value: number | string) => Number(value).toLocaleString("zh-CN");
const busyJob = (job: LibraryJob) => job.status === "queued" || job.status === "running";

export function LibraryManager() {
  const [data, setData] = useState<LibraryOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [confirmWithdraw, setConfirmWithdraw] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const alive = useRef(true);
  const sequence = useRef(0);
  const uploadController = useRef<AbortController | null>(null);
  const operation = useRef(false);

  const report = useCallback((cause: unknown) => {
    if (cause instanceof LibraryApiError && cause.status === 401) {
      window.location.assign("/login?next=/library");
      return;
    }
    if (cause instanceof DOMException && cause.name === "AbortError") return;
    if (alive.current) setError(cause instanceof Error ? cause.message : "暂时无法连接，请稍后重试。");
  }, []);
  const refresh = useCallback(async (signal?: AbortSignal) => {
    const request = ++sequence.current;
    try {
      const result = await loadLibrary(signal);
      if (alive.current && request === sequence.current) { setData(result); setError(null); }
    } catch (cause) { if (request === sequence.current) report(cause); }
  }, [report]);

  useEffect(() => {
    alive.current = true;
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => { alive.current = false; controller.abort(); uploadController.current?.abort(); };
  }, [refresh]);
  const pollingInterval = data?.jobs.some(busyJob) ? 4000 : 30000;
  useEffect(() => {
    const controller = new AbortController();
    let timer: number;
    const poll = async () => {
      await refresh(controller.signal);
      // A repeated connection error must not stop polling. Schedule after the
      // request finishes, so a slow database cannot accumulate overlapping polls.
      if (!controller.signal.aborted) timer = window.setTimeout(poll, pollingInterval);
    };
    timer = window.setTimeout(poll, pollingInterval);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [pollingInterval, refresh]);

  async function upload(files: readonly File[]) {
    if (operation.current || !files.length) return;
    operation.current = true;
    const controller = new AbortController();
    uploadController.current = controller;
    setPending("upload"); setError(null); setNotice(null);
    let accepted = 0; let reused = 0;
    const failures: string[] = [];
    try {
      for (let index = 0; index < files.length; index++) {
        if (controller.signal.aborted) break;
        setUploadProgress(`上传 ${index + 1}/${files.length} · ${files[index].name}`);
        try {
          const result = await uploadLibraryFile(files[index], controller.signal);
          if (result.reused) reused++; else accepted++;
        } catch (cause) {
          if (cause instanceof LibraryApiError && cause.status === 401) throw cause;
          if (controller.signal.aborted) throw cause;
          failures.push(`${files[index].name}：${cause instanceof Error ? cause.message : "上传失败"}`);
        }
      }
      if (alive.current) {
        if (accepted || reused) setNotice(`${accepted ? `${accepted} 份文件已接收，后台会依次合并处理。` : ""}${reused ? `${reused} 份文件已存在，未重复加入。` : ""}`);
        await refresh();
        if (failures.length) setError(failures.join("\n"));
      }
    } catch (cause) { report(cause); }
    finally {
      uploadController.current = null;
      operation.current = false;
      if (alive.current) { setPending(null); setUploadProgress(null); if (input.current) input.current.value = ""; }
    }
  }

  async function operate(key: string, action: () => Promise<unknown>, message: string) {
    if (operation.current) return;
    operation.current = true;
    setPending(key); setError(null); setNotice(null);
    try { await action(); if (alive.current) { setConfirmWithdraw(null); setNotice(message); await refresh(); } }
    catch (cause) { report(cause); }
    finally { operation.current = false; if (alive.current) setPending(null); }
  }

  const activeCount = data?.files.filter(file => file.status === "active").length ?? 0;
  const unresolved = data?.jobs.filter(job => job.status === "awaiting_resolution") ?? [];

  return <main className="library-page">
    <header className="library-topbar">
      <Link href="/" className="library-brand"><span className="brand-mark"><Route size={18} /></span>RouteFrom</Link>
      <span className="library-location">个人足迹库</span>
      <div className="library-account"><span>{data?.user.name ?? ""}</span>
        <button type="button" disabled={!!pending} aria-label="退出登录" title="退出登录" onClick={() => void operate("logout", async () => {
          const result = await authClient.signOut();
          if (result.error) throw new Error("退出失败，请重试。");
          window.location.assign("/login");
        }, "已退出登录。")}><LogOut size={16} /></button>
      </div>
    </header>
    <div className="library-content">
      <div className="library-title-row"><div><span className="eyebrow">原始记录 / 个人足迹</span><h1>你的足迹，从这里汇合。</h1>
        <p>多次导出的 CSV 汇入同一个足迹库，重叠观测只计算一次。</p></div>
        <Link href="/" className="library-map-link"><ArrowLeft size={16} />回到地图</Link>
      </div>
      {error && <div className="library-message error" role="alert"><AlertTriangle size={18} /><span>{error}</span><button type="button" onClick={() => void refresh()}>重新读取</button></div>}
      {notice && <div className="library-message" role="status"><Check size={17} /><span>{notice}</span></div>}
      <div className="library-columns">
        <div className="library-main">
          <section className={`library-upload ${dragging ? "is-dragging" : ""}`} aria-labelledby="upload-title"
            onDragOver={event => { event.preventDefault(); if (!pending) setDragging(true); }}
            onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false); }}
            onDrop={event => { event.preventDefault(); setDragging(false); void upload(Array.from(event.dataTransfer.files)); }}>
            <Upload size={26} strokeWidth={1.4} />
            <div><h2 id="upload-title">加入原始 CSV</h2><p>拖入一份或多份灵敢足迹导出，也可以选择文件。每份不超过 80 MB。</p></div>
            <input ref={input} type="file" accept=".csv,text/csv" multiple tabIndex={-1} aria-label="选择原始 CSV" className="library-file-input" disabled={!!pending}
              onChange={event => void upload(Array.from(event.target.files ?? []))} />
            <button className="library-primary" type="button" disabled={!!pending} onClick={() => input.current?.click()}>
              {pending === "upload" ? <LoaderCircle size={15} className="is-spinning" /> : <Upload size={15} />}{pending === "upload" ? "正在上传" : "选择文件"}
            </button>
            {uploadProgress && <p className="library-upload-progress" role="status">{uploadProgress}</p>}
          </section>
          <section className="library-files" aria-labelledby="files-title">
            <div className="library-section-heading"><h2 id="files-title">原始文件 <small>{data ? `${activeCount} 份已加入 / ${data.files.length} 份留存` : ""}</small></h2>
              <button type="button" className="library-text-button" onClick={() => void refresh()}><RefreshCw size={14} />刷新</button></div>
            {!data && !error && <div className="library-empty"><LoaderCircle size={20} className="is-spinning" /><p>正在读取你的足迹库…</p></div>}
            {data && !data.files.length && <div className="library-empty"><FileText size={28} strokeWidth={1.3} /><h3>还没有加入原始记录</h3><p>上传后会显示文件的时间范围。处理完成前，可以先离开页面，后台会继续运行。</p></div>}
            {data?.files.map(file => {
              const coverage = fileCoverage(data.files, file);
              const latest = data.jobs.find(job => job.dataset_id === file.dataset_id);
              const withdrawn = file.status === "withdrawn";
              return <article className={`library-file ${withdrawn ? "is-withdrawn" : ""}`} key={file.id}>
                <FileText size={19} strokeWidth={1.5} className="library-file-glyph" />
                <div className="library-file-info"><h3 title={file.name}>{file.name}</h3>
                  <p>{count(file.point_count)} 个原始观测 <span>·</span> {(Number(file.source_size_bytes) / 1024 / 1024).toFixed(2)} MB</p>
                  <div className="library-file-time"><time>{instant(file.recorded_from)}</time><span>—</span><time>{instant(file.recorded_to)}</time></div>
                  <div className="library-coverage" aria-hidden="true">{coverage && <span style={{ left: `${coverage.left}%`, width: `${coverage.width}%` }} />}</div>
                  {latest && busyJob(latest) && <p className="library-file-pending">{jobStageLabel(latest.stage)} · 旧地图仍可查看</p>}
                </div>
                <div className="library-file-actions"><span className={`library-badge ${withdrawn ? "muted" : "ready"}`}>{withdrawn ? "已撤销" : "已加入"}</span>
                  {confirmWithdraw === file.id ? <div className="library-withdraw-confirm"><span>原文件会保留，共有观测不会移除。</span>
                    <button type="button" disabled={!!pending} onClick={() => void operate(file.id, () => changeLibraryFile(file.id, "withdraw"), "文件已撤销，后台将重新计算足迹；原始记录仍保留。")}>确认撤销</button>
                    <button type="button" disabled={!!pending} onClick={() => setConfirmWithdraw(null)}>取消</button></div>
                    : <button type="button" disabled={!!pending} className="library-text-button" onClick={() => withdrawn
                      ? void operate(file.id, () => changeLibraryFile(file.id, "restore"), "已排队恢复文件，后台将重新计算足迹。") : setConfirmWithdraw(file.id)}>
                      <Undo2 size={13} />{withdrawn ? "恢复加入" : "撤销加入"}</button>}
                </div>
              </article>;
            })}
          </section>
          {unresolved.map(job => <ConflictChooser key={job.id} job={job} disabled={!!pending}
            onSubmit={decisions => operate(job.id, () => updateLibraryJob(job.id, "resolve", decisions), "选择已保存，后台继续处理。仍有冲突时会显示下一批。") } />)}
        </div>
        <aside className="library-side">
          <section className="library-published"><span className="eyebrow">当前地图版本</span>
            <h2>{data?.published ? `${count(data.published.point_count)} 个唯一观测` : "尚未发布足迹"}</h2>
            <p>{data?.published ? `发布于 ${instant(data.published.published_at, true)}。新文件处理期间继续保留此版本。`
              : "上传后，后台先检查原始记录，再合并并识别足迹。成功处理后才发布新地图。"}</p>
            {data?.published && <Link href="/">查看地图<ArrowUpRight size={15} /></Link>}
          </section>
          <section className="library-jobs" aria-labelledby="jobs-title"><div className="library-section-heading"><h2 id="jobs-title">最近处理</h2><small>最近 50 次</small></div>
            {!data?.jobs.length && <p className="library-muted">上传文件后，进度会出现在这里。</p>}
            {data?.jobs.map(job => <article className={`library-job ${job.status}`} key={job.id}>
              <div className="library-job-heading"><span className={`library-badge ${job.status === "succeeded" ? "ready" : job.status === "failed" ? "danger" : job.status === "awaiting_resolution" ? "warning" : "muted"}`}>{jobStatusLabel(job.status)}</span><time>{instant(job.created_at, true)}</time></div>
              <h3 title={job.name ?? "重新计算个人足迹库"}>{job.name ?? "重新计算个人足迹库"}</h3>
              <p>{job.status === "superseded" ? "库内容已更新，本次结果没有覆盖当前地图。" : jobStageLabel(job.stage)}</p>
              {busyJob(job) && <div className="library-job-progress" role="progressbar" aria-label={jobStageLabel(job.stage)} aria-valuenow={job.progress} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${job.progress}%` }} /></div>}
              {job.status === "succeeded" && job.metrics.added_observations !== undefined && <p className="library-job-outcome">新增 {count(job.metrics.added_observations)} 个观测 · 重复 {count(job.metrics.duplicate_rows ?? 0)} 行</p>}
              {job.status === "awaiting_resolution" && <a href={`#conflict-${job.id}`} className="library-text-button">选择本次要使用的观测<ArrowUpRight size={13} /></a>}
              {job.status === "failed" && <><p className="library-job-outcome">上一版地图未被替换。请确认 CSV 是完整的灵敢原始导出；临时失败可重试。</p>
                <button type="button" disabled={!!pending} className="library-text-button" onClick={() => void operate(job.id, () => updateLibraryJob(job.id, "retry"), "已重新排队处理。")}>重试处理<RefreshCw size={12} /></button></>}
            </article>)}
          </section>
        </aside>
      </div>
    </div>
  </main>;
}

function ConflictChooser({ job, disabled, onSubmit }: { job: LibraryJob; disabled: boolean; onSubmit: (decisions: Record<string, string>) => Promise<void> }) {
  const [selections, setSelections] = useState<Record<string, string>>({});
  const [visibleCount, setVisibleCount] = useState(20);
  const conflicts = job.metrics.conflicts ?? [];
  const accepted = validConflictDecisions(conflicts, selections);
  const selectedCount = Object.keys(accepted).length;
  return <section className="library-conflicts" id={`conflict-${job.id}`} aria-labelledby={`conflict-title-${job.id}`}>
    <div className="library-section-heading"><h2 id={`conflict-title-${job.id}`}><AlertTriangle size={17} />选择同一时刻的观测</h2><span className="library-badge warning">{count(job.metrics.remaining_conflict_count ?? conflicts.length)} 组待决</span></div>
    <p className="library-muted">同一时刻存在不同记录，原始观测都会保留。选择本次处理要使用的点；不选的部分会继续等待，上一版地图不受影响。</p>
    <fieldset disabled={disabled} className="library-conflict-fields">
      {conflicts.slice(0, visibleCount).map(conflict => <fieldset key={conflict.epochMs}><legend>{instant(new Date(conflict.epochMs).toISOString(), true, true)} <small>{conflict.epochMs % 1000 ? `.${String(conflict.epochMs % 1000).padStart(3, "0")} 秒` : ""}</small></legend>
        {conflict.candidates.map((candidate, index) => <label key={candidate.fingerprint}>
          <input type="radio" name={`${job.id}-${conflict.epochMs}`} value={candidate.fingerprint} checked={selections[String(conflict.epochMs)] === candidate.fingerprint}
            onChange={() => setSelections(previous => ({ ...previous, [String(conflict.epochMs)]: candidate.fingerprint }))} />
          <span>观测 {index + 1}<small>纬度 {candidate.latitude.toFixed(6)} · 经度 {candidate.longitude.toFixed(6)}</small></span>
          <em>{candidate.accuracyMeters === null ? "精度未知" : `精度约 ${candidate.accuracyMeters.toFixed(1)} m`}</em>
        </label>)}
      </fieldset>)}
      {visibleCount < conflicts.length && <button type="button" className="library-text-button" onClick={() => setVisibleCount(value => Math.min(conflicts.length, value + 20))}>再显示 20 组</button>}
      <div className="library-conflict-submit"><span>已选择 {selectedCount} 组{conflicts.length < (job.metrics.remaining_conflict_count ?? 0) ? " · 提交后可继续处理下一批" : ""}</span>
        <button type="button" className="library-primary" disabled={!selectedCount || disabled} onClick={() => void onSubmit(accepted)}>保存选择并继续<ArrowUpRight size={14} /></button></div>
    </fieldset>
  </section>;
}
