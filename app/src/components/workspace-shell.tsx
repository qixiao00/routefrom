"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useState } from "react";
import { motion } from "motion/react";
import {
  AlertTriangle,
  CalendarRange,
  Check,
  ChevronDown,
  CircleHelp,
  Cloud,
  Command,
  Database,
  Eye,
  EyeOff,
  Focus,
  Layers3,
  LoaderCircle,
  Map as MapIcon,
  MapPin,
  MoreHorizontal,
  PanelLeftClose,
  Plus,
  RefreshCw,
  Route,
  Search,
  Settings2,
  Trash2,
  TriangleAlert,
} from "lucide-react";

import { loadWorkspacePreview } from "@/lib/workspace-client";
import {
  overlapsSelection,
  selectPaths,
  selectedDistanceMeters,
  type PreviewGap,
  type PreviewPlace,
  type PreviewStay,
} from "@/lib/workspace-data";
import { type LayerId, useWorkspaceStore } from "@/lib/workspace-store";
import type { TimeRange } from "@/lib/workspace-query";

import { Timeline } from "./timeline";

const MapCanvas = dynamic(() => import("./map-canvas").then((module) => module.MapCanvas), {
  ssr: false,
  loading: () => <div className="map-loading">正在准备地图画布…</div>,
});

const layerDefinitions: Array<{
  id: LayerId;
  label: string;
  description: string;
  color: string;
  icon: typeof Route;
}> = [
  { id: "track", label: "观测轨迹", description: "连续确认段", color: "mint", icon: Route },
  { id: "stays", label: "静止事件", description: "访问、暂停与未决", color: "amber", icon: MapPin },
  { id: "gaps", label: "未知缺口", description: "不计入确认统计", color: "slate", icon: TriangleAlert },
  { id: "places", label: "常去地点", description: "概率聚类结果", color: "blue", icon: Focus },
];

function localInputValue(instant: string): string {
  const date = new Date(instant);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function updateLocalRange(range: TimeRange, field: keyof TimeRange, value: string): TimeRange {
  const parsed = new Date(value);
  return Number.isFinite(parsed.getTime()) ? { ...range, [field]: parsed.toISOString() } : range;
}

function formatInstant(instant: string, withTime = false): string {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    ...(withTime ? { hour: "2-digit", minute: "2-digit", hour12: false } : {}),
  }).format(new Date(instant));
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  return `${hours} 小时 ${minutes} 分`;
}

function formatDistance(meters: number): string {
  return meters >= 1000 ? `${(meters / 1000).toFixed(1)} km` : `${Math.round(meters)} m`;
}

type SelectedEntity = PreviewStay | PreviewGap | PreviewPlace;

export function WorkspaceShell() {
  const [activeTool, setActiveTool] = useState("layers");
  const [fitRequest, setFitRequest] = useState(0);
  const {
    status,
    error,
    data,
    ranges,
    visibleLayers,
    selection,
    cursorTime,
    mapView,
    setLoading,
    setData,
    setError,
    toggleLayer,
    addRange,
    updateRange,
    removeRange,
    useSuggestedRange,
    setSelection,
    setCursorTime,
    setMapView,
  } = useWorkspaceStore();

  useEffect(() => {
    const controller = new AbortController();
    setLoading();
    loadWorkspacePreview(controller.signal).then(setData).catch((cause: unknown) => {
      if (cause instanceof DOMException && cause.name === "AbortError") return;
      setError(cause instanceof Error ? cause.message : "无法读取足迹工作区。");
    });
    return () => controller.abort();
  }, [setData, setError, setLoading]);

  const selectedPaths = useMemo(
    () => (data ? selectPaths(data.paths, ranges) : []),
    [data, ranges],
  );
  const selectedStays = useMemo(
    () => data?.stays.filter((stay) => overlapsSelection(stay.start, stay.end, ranges)) ?? [],
    [data, ranges],
  );
  const selectedGaps = useMemo(
    () => data?.gaps.filter((gap) => overlapsSelection(gap.start, gap.end, ranges)) ?? [],
    [data, ranges],
  );
  const distance = useMemo(() => selectedDistanceMeters(selectedPaths), [selectedPaths]);
  const selectedDuration = useMemo(
    () => ranges.reduce((total, range) => total + (Date.parse(range.end) - Date.parse(range.start)) / 1000, 0),
    [ranges],
  );
  const selectedEntity: SelectedEntity | null = useMemo(() => {
    if (!data || !selection) return null;
    if (selection.kind === "stay") return data.stays.find((item) => item.id === selection.id) ?? null;
    if (selection.kind === "gap") return data.gaps.find((item) => item.id === selection.id) ?? null;
    if (selection.kind === "place") return data.places.find((item) => item.id === selection.id) ?? null;
    return null;
  }, [data, selection]);

  return (
    <main className="workspace">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark"><Route size={17} /></div>
          <span>RouteFrom</span>
          <em>MVP</em>
        </div>

        <button className="view-switcher" type="button">
          <span className={`status-dot ${status}`} />
          {data?.dataset.name ?? "本地足迹"}
          <ChevronDown size={14} />
        </button>

        <div className="topbar-center">
          <button className="date-control" type="button">
            <CalendarRange size={15} />
            <span>{ranges.length > 1 ? `${ranges.length} 个不连续区间` : ranges[0] ? `${formatInstant(ranges[0].start)} — ${formatInstant(ranges[0].end)}` : "选择时间"}</span>
            <ChevronDown size={13} />
          </button>
          <button className="command-search" type="button" disabled>
            <Search size={15} />
            <span>搜索将在下一版开放</span>
            <kbd><Command size={11} /> K</kbd>
          </button>
        </div>

        <div className="topbar-actions">
          <span className="sync-state"><Cloud size={14} />视图已保存在本机</span>
          <button className="icon-button" type="button" aria-label="帮助"><CircleHelp size={16} /></button>
          <button className="avatar" type="button">RF</button>
        </div>
      </header>

      <aside className="tool-rail" aria-label="工作区工具">
        {[
          { id: "layers", label: "图层", icon: Layers3 },
          { id: "data", label: "数据", icon: Database },
          { id: "map", label: "地图", icon: MapIcon },
          { id: "style", label: "样式", icon: Settings2 },
        ].map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            className={activeTool === id ? "active" : ""}
            onClick={() => setActiveTool(id)}
            aria-label={label}
            title={label}
            type="button"
          >
            <Icon size={18} />
          </button>
        ))}
        <button className="rail-bottom" type="button" aria-label="收起侧栏"><PanelLeftClose size={18} /></button>
      </aside>

      <motion.aside
        className="layer-panel"
        initial={{ opacity: 0, x: -8 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ duration: 0.32, ease: "easeOut" }}
      >
        <div className="panel-heading">
          <div><span>观察范围</span><h1>时间与图层</h1></div>
          <button className="icon-button" type="button" aria-label="更多选项"><MoreHorizontal size={17} /></button>
        </div>

        <div className="dataset-card">
          <div className="dataset-icon"><Database size={16} /></div>
          <div>
            <strong>{data?.dataset.name ?? "正在读取"}</strong>
            <span>{data ? `${data.dataset.pointCount.toLocaleString("zh-CN")} 个原始观测` : "本地私有数据"}</span>
          </div>
          <span className={`ready-badge ${status}`}>
            {status === "loading" ? <LoaderCircle size={11} className="spin" /> : status === "error" ? <AlertTriangle size={11} /> : <Check size={11} />}
            {status === "loading" ? "读取" : status === "error" ? "异常" : "就绪"}
          </span>
        </div>

        <section className="panel-section">
          <div className="section-title"><span>可视图层</span><small>{selectedPaths.length} 条连续路径</small></div>
          <div className="layer-list">
            {layerDefinitions.map(({ id, label, description, icon: Icon, color }) => {
              const visible = visibleLayers[id];
              const count = id === "stays" ? selectedStays.length : id === "gaps" ? selectedGaps.length : id === "places" ? data?.places.length ?? 0 : selectedPaths.reduce((sum, path) => sum + path.vertices.length, 0);
              return (
                <button key={id} type="button" className={`layer-item ${visible ? "is-visible" : ""}`} onClick={() => toggleLayer(id)}>
                  <span className={`layer-icon ${color}`}><Icon size={15} /></span>
                  <span className="layer-name">{label}<small>{description} · {count.toLocaleString("zh-CN")}</small></span>
                  {visible ? <Eye size={15} /> : <EyeOff size={15} />}
                </button>
              );
            })}
          </div>
        </section>

        <section className="panel-section slices-section">
          <div className="section-title"><span>时间切片</span><button type="button" onClick={addRange}><Plus size={14} />添加区间</button></div>
          <div className="range-list">
            {ranges.map((range, index) => (
              <div className="range-editor" key={`${range.start}-${range.end}`}>
                <span className={`slice-color ${index % 2 === 0 ? "mint" : "blue"}`} />
                <div className="range-fields">
                  <label><span>开始</span><input type="datetime-local" value={localInputValue(range.start)} onChange={(event) => updateRange(index, updateLocalRange(range, "start", event.target.value))} /></label>
                  <label><span>结束</span><input type="datetime-local" value={localInputValue(range.end)} onChange={(event) => updateRange(index, updateLocalRange(range, "end", event.target.value))} /></label>
                </div>
                <button type="button" className="range-remove" onClick={() => removeRange(index)} disabled={ranges.length === 1} aria-label={`删除时间区间 ${index + 1}`}><Trash2 size={13} /></button>
              </div>
            ))}
          </div>
        </section>

        <button className="new-view-button" type="button" onClick={useSuggestedRange}><RefreshCw size={14} />回到最近两周</button>
      </motion.aside>

      <section className="map-stage">
        {data ? (
          <MapCanvas
            data={data}
            selectedPaths={selectedPaths}
            selectedStays={selectedStays}
            selectedGaps={selectedGaps}
            visibleLayers={visibleLayers}
            selection={selection}
            mapView={mapView}
            fitRequest={fitRequest}
            onMapViewChange={setMapView}
            onSelect={setSelection}
          />
        ) : <div className="map-loading">{status === "error" ? "足迹尚未载入" : "正在读取真实足迹…"}</div>}
        <div className="map-vignette" />
        <div className="map-toolbar">
          <button className="active" type="button" onClick={() => setFitRequest((value) => value + 1)}><Focus size={16} />聚焦所选时间</button>
          <button type="button" aria-label="地图设置"><Settings2 size={16} /></button>
        </div>
        <div className="map-stat">
          <span>所选确认距离</span>
          <strong>{distance >= 1000 ? (distance / 1000).toFixed(1) : Math.round(distance)} <small>{distance >= 1000 ? "km" : "m"}</small></strong>
          <div><span>{formatDuration(selectedDuration)}</span><span>{selectedGaps.length} 个未知缺口</span></div>
        </div>
        {status === "error" && (
          <div className="workspace-error" role="alert">
            <AlertTriangle size={18} />
            <div><strong>本地足迹尚未就绪</strong><span>{error}</span><code>routefrom-preview data/raw/你的导出.csv</code></div>
          </div>
        )}
      </section>

      <motion.aside
        className="inspector"
        initial={{ opacity: 0, x: 8 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ duration: 0.32, ease: "easeOut", delay: 0.05 }}
      >
        <div className="panel-heading compact">
          <div><span>证据检查器</span><h2>{selectedEntity ? ("name" in selectedEntity ? selectedEntity.name : selectedEntity.id.startsWith("gap") ? "未知时间" : "静止事件") : "所选时间"}</h2></div>
          <button className="icon-button" type="button" aria-label="更多选项"><MoreHorizontal size={17} /></button>
        </div>

        {selectedEntity ? (
          <EntityInspector entity={selectedEntity} />
        ) : (
          <>
            <div className="selection-overview">
              <span className="selection-glyph"><CalendarRange size={21} /></span>
              <strong>{ranges.length} 个区间</strong>
              <span>地图和时间轴仅显示这些时间，区间之间不会连线。</span>
            </div>
            <div className="metric-grid">
              <div><span>确认距离</span><strong>{formatDistance(distance)}</strong></div>
              <div><span>静止事件</span><strong>{selectedStays.length}</strong></div>
              <div><span>未知缺口</span><strong>{selectedGaps.length}</strong></div>
              <div><span>轨迹变体</span><strong>{data?.processing.trajectoryVariant.replace("_gps", "") ?? "—"}</strong></div>
            </div>
            <section className="inspector-section evidence-section">
              <span className="eyebrow">当前处理证据</span>
              <div className="evidence-row"><span>算法版本</span><code>{data?.processing.algorithmVersion.split("-").at(-1) ?? "—"}</code></div>
              <div className="evidence-row"><span>平滑置信度</span><strong>{data ? `${Math.round(data.processing.smoothingConfidence * 100)}%` : "—"}</strong></div>
              <div className="evidence-row"><span>播放游标</span><strong>{cursorTime ? formatInstant(cursorTime, true) : "未设置"}</strong></div>
            </section>
          </>
        )}
      </motion.aside>

      <Timeline data={data} ranges={ranges} cursorTime={cursorTime} selection={selection} onCursorChange={setCursorTime} onSelect={setSelection} />
    </main>
  );
}

function EntityInspector({ entity }: { entity: SelectedEntity }) {
  if ("startPosition" in entity) {
    return (
      <>
        <div className="place-preview gap-preview"><div className="place-orbit"><TriangleAlert size={19} /></div><span>未观测</span><span>{formatDuration((Date.parse(entity.end) - Date.parse(entity.start)) / 1000)}</span></div>
        <div className="metric-grid"><div><span>开始</span><strong>{formatInstant(entity.start, true)}</strong></div><div><span>恢复</span><strong>{formatInstant(entity.end, true)}</strong></div><div><span>置信度</span><strong>{Math.round(entity.confidence * 100)}%</strong></div><div><span>统计</span><strong>不计入</strong></div></div>
        <div className="truth-note"><TriangleAlert size={15} /><span>这段时间可能是手机关机、未携带或系统未采样。虚线只表达上下文，不代表真实移动。</span></div>
      </>
    );
  }
  if ("visitCount" in entity) {
    return (
      <>
        <div className="place-preview"><div className="place-orbit"><MapPin size={19} /></div><span>{entity.position[1].toFixed(4)}° N</span><span>{entity.position[0].toFixed(4)}° E</span></div>
        <div className="metric-grid"><div><span>累计停留</span><strong>{formatDuration(entity.dwellSeconds)}</strong></div><div><span>访问次数</span><strong>{entity.visitCount}</strong></div><div><span>常去概率</span><strong>{Math.round(entity.frequentProbability * 100)}%</strong></div><div><span>聚类置信度</span><strong>{Math.round(entity.confidence * 100)}%</strong></div></div>
      </>
    );
  }
  return (
    <>
      <div className="place-preview"><div className="place-orbit"><MapPin size={19} /></div><span>{entity.kind === "visit" ? "可能访问" : entity.kind === "transport_pause" ? "交通暂停" : "未决静止"}</span><span>{formatDuration(entity.durationSeconds)}</span></div>
      <div className="metric-grid"><div><span>开始</span><strong>{formatInstant(entity.start, true)}</strong></div><div><span>结束</span><strong>{formatInstant(entity.end, true)}</strong></div><div><span>访问概率</span><strong>{Math.round(entity.visitProbability * 100)}%</strong></div><div><span>置信度</span><strong>{Math.round(entity.confidence * 100)}%</strong></div></div>
    </>
  );
}
