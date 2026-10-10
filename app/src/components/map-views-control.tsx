"use client";

import { useEffect, useRef, useState } from "react";
import * as Popover from "@radix-ui/react-popover";
import { Check, Cloud, LoaderCircle, RefreshCw, Save, Trash2 } from "lucide-react";
import { serializeMapView, type MapViewCollection, type MapViewConfig } from "@/lib/map-view-config";
import { MapViewSaveQueue, type ViewSyncState } from "@/lib/map-view-save-queue";
import { createMapView, loadMapViews, removeMapView, saveActiveMapView } from "@/lib/map-views-client";
import { useWorkspaceStore } from "@/lib/workspace-store";
import { WorkspaceBootstrapError } from "@/lib/workspace-client";

export function MapViewsControl({ accountSubject, ready }: { accountSubject: string; ready: boolean }) {
  const [sync, setSync] = useState<ViewSyncState>({ status: "loading", message: null });
  const [collection, setCollection] = useState<MapViewCollection | null>(null);
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [operationError, setOperationError] = useState<string | null>(null);
  const [recovery, setRecovery] = useState(false);
  const queue = useRef<MapViewSaveQueue | null>(null);
  const operationBusy = useRef(false);
  const reconnect = useRef<(restore: boolean) => Promise<void>>(async () => {});

  useEffect(() => {
    if (!ready) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let abort = new AbortController();
    const current = () => serializeMapView(useWorkspaceStore.getState());
    const ownerMatches = () => useWorkspaceStore.getState().ownerId === accountSubject;
    const isLive = () => !disposed && ownerMatches();
    const fail = (error: unknown) => {
      if (!isLive()) return;
      if (error instanceof WorkspaceBootstrapError && error.code === "authentication_required") { window.location.assign("/login"); return; }
      if (error instanceof WorkspaceBootstrapError && error.code === "account_changed") { window.location.reload(); return; }
      setSync({ status: error instanceof WorkspaceBootstrapError && error.code === "view_conflict" ? "conflict" : "error",
        message: error instanceof Error ? error.message : "无法读取账号视图，本地改动仍保留。" });
    };
    const schedule = () => {
      if (timer) clearTimeout(timer);
      const state = useWorkspaceStore.getState();
      if (!isLive() || state.status !== "ready") return;
      queue.current?.update(current());
      if (queue.current) state.setCloudViewPending(queue.current.dirty);
      timer = setTimeout(() => { if (isLive()) void queue.current?.flush(); }, 1200);
    };
    const connect = async (restore: boolean, initial = false) => {
      queue.current?.dispose(); queue.current = null;
      if (timer) clearTimeout(timer);
      abort.abort(); abort = new AbortController();
      const signal = abort.signal;
      const before = JSON.stringify(current());
      const pendingLocal = useWorkspaceStore.getState().cloudViewPending;
      setSync({ status: "loading", message: null });
      try {
        const result = await loadMapViews(accountSubject, signal);
        if (!isLive() || signal.aborted) return;
        setCollection(result);
        // Don't overwrite edits made while the first account read was pending.
        if (restore && result.config && initial && JSON.stringify(result.config) !== JSON.stringify(current()) &&
          (pendingLocal || before !== JSON.stringify(current()))) {
          setSync({ status: "conflict", message: "本机有尚未同步的地图改动，请选择要保留的视图。" }); return;
        }
        if (restore && result.config) useWorkspaceStore.getState().applyMapViewConfig(result.config);
        queue.current = new MapViewSaveQueue(result.revision, result.config,
          async (config, revision) => {
            try { return await saveActiveMapView(accountSubject, config, revision, signal); }
            catch (error) {
              if (isLive() && error instanceof WorkspaceBootstrapError && error.code === "authentication_required") window.location.assign("/login");
              if (isLive() && error instanceof WorkspaceBootstrapError && error.code === "account_changed") window.location.reload();
              throw error;
            }
          }, state => { if (isLive()) { setSync(state); if (state.status === "saved") useWorkspaceStore.getState().setCloudViewPending(false); } });
        schedule();
      } catch (error) { if (!signal.aborted) fail(error); }
    };
    reconnect.current = restore => connect(restore);
    const unsubscribe = useWorkspaceStore.subscribe((state, previous) => {
      if (state.ownerId !== accountSubject) { queue.current?.dispose(); abort.abort(); return; }
      if (state.mapView !== previous.mapView || state.ranges !== previous.ranges || state.visibleLayers !== previous.visibleLayers || state.panels !== previous.panels) schedule();
    });
    const flush = () => { if (isLive()) void queue.current?.flush(); };
    const visibility = () => { if (document.visibilityState === "hidden") flush(); };
    window.addEventListener("pagehide", flush);
    document.addEventListener("visibilitychange", visibility);
    void connect(true, true);
    return () => {
      disposed = true; if (timer) clearTimeout(timer); unsubscribe(); queue.current?.dispose(); queue.current = null;
      abort.abort(); window.removeEventListener("pagehide", flush); document.removeEventListener("visibilitychange", visibility);
    };
  }, [accountSubject, ready]);

  const act = async (operation: () => Promise<void>) => {
    if (operationBusy.current || useWorkspaceStore.getState().ownerId !== accountSubject) return;
    operationBusy.current = true;
    setBusy(true); setOperationError(null);
    try { await operation(); }
    catch (error) {
      if (error instanceof WorkspaceBootstrapError && error.code === "account_changed") window.location.reload();
      else if (error instanceof WorkspaceBootstrapError && error.code === "authentication_required") window.location.assign("/login");
      else setOperationError(error instanceof Error ? error.message : "操作失败，请重试。");
    }
    finally { operationBusy.current = false; setBusy(false); }
  };
  const recover = (restore: boolean) => act(async () => { await reconnect.current(restore); setRecovery(false); });
  const restoreView = (config: MapViewConfig) => { useWorkspaceStore.getState().applyMapViewConfig(config); setOpen(false); };
  const labels = { loading: "正在读取账号视图", saved: "视图已保存到账号", pending: "视图待保存", saving: "正在保存视图", error: "视图未同步", conflict: "视图版本冲突" };
  const problem = sync.status === "error" || sync.status === "conflict";

  if (!ready) return <button className="map-view-sync" type="button" aria-label="地图视图与同步" disabled><Cloud size={14} /><span>地图视图</span></button>;

  return <Popover.Root open={open} onOpenChange={next => {
    setOpen(next); setRecovery(false);
    if (next && collection && !busy) void act(async () => {
      // Refresh bookmarks without changing the write revision or local camera.
      const result = await loadMapViews(accountSubject); setCollection(result);
    });
  }}>
    <Popover.Trigger asChild><button className={`map-view-sync ${problem ? "has-error" : ""}`} type="button" aria-label="地图视图与同步" disabled={!ready}>
      {sync.status === "loading" || sync.status === "saving" ? <LoaderCircle size={14} className="spin" /> : <Cloud size={14} />}
      <span>{ready ? labels[sync.status] : "地图视图"}</span>
    </button></Popover.Trigger>
    <Popover.Portal><Popover.Content className="map-views-popover" align="end" sideOffset={12} collisionPadding={12}>
      <div className="map-views-heading"><div><small>你的地图，随账号保留</small><h2>保存的视图</h2></div><Cloud size={20} /></div>
      <p className="map-views-hint">保存地球视角、图层开关、任意时间切片和面板开合。不会改变原始足迹或算法结果。</p>
      <div className={`map-views-status ${problem ? "has-error" : ""}`} role="status">
        <strong>{labels[sync.status]}</strong><span>{sync.message ?? "切换浏览器或设备，登录后恢复最近的视图。"}</span>
        {problem && !recovery && <button type="button" disabled={busy} onClick={() => void act(async () => {
          const result = await loadMapViews(accountSubject); setCollection(result); setRecovery(true);
        })}><RefreshCw size={13} />读取账号视图</button>}
        {problem && recovery && <div className="map-view-recovery">
          <p>账号视图已读取。请选择保留哪一份；保存本地视图会替换账号中的最近视图。</p>
          <button type="button" disabled={busy} onClick={() => void recover(true)}>恢复账号视图</button>
          <button type="button" disabled={busy} onClick={() => void recover(false)}>保存当前本地视图</button>
        </div>}
      </div>
      <form className="map-view-save-form" onSubmit={event => {
        event.preventDefault();
        void act(async () => {
          const saved = await createMapView(accountSubject, title, serializeMapView(useWorkspaceStore.getState()));
          setCollection(previous => previous ? { ...previous, views: [saved, ...previous.views] } : previous); setTitle("");
        });
      }}>
        <label htmlFor="map-view-title">为当前视图命名</label>
        <div><input id="map-view-title" value={title} onChange={event => setTitle(event.target.value)} maxLength={80} placeholder="例如：夏天的通勤路线" disabled={busy || !collection} />
          <button type="submit" disabled={busy || !collection || !title.trim()}><Save size={14} />保存</button></div>
      </form>
      {operationError && <p className="map-views-operation-error" role="alert">{operationError}</p>}
      <div className="saved-map-view-list">
        {collection?.views.length ? collection.views.map(view => <div className="saved-map-view" key={view.id}>
          <button type="button" disabled={busy} onClick={() => restoreView(view.config)}><strong>{view.title}</strong>
            <small>{view.config.ranges.length} 个时间切片 · {view.config.map.camera.zoom.toFixed(1)} 级视角</small></button>
          <button type="button" className="saved-map-view-delete" aria-label={`删除视图 ${view.title}`} disabled={busy} onClick={() => {
            if (!window.confirm(`删除视图「${view.title}」？这不会删除足迹数据。`)) return;
            void act(async () => { await removeMapView(accountSubject, view.id); setCollection(previous => previous ? { ...previous, views: previous.views.filter(item => item.id !== view.id) } : previous); });
          }}><Trash2 size={14} /></button>
        </div>) : <p className="saved-map-view-empty"><Check size={16} />最近视图自动保存；想反复查看的范围可以另存为命名视图。</p>}
      </div>
      <Popover.Arrow className="map-views-arrow" />
    </Popover.Content></Popover.Portal>
  </Popover.Root>;
}
