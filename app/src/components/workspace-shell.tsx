"use client";

import dynamic from "next/dynamic";
import { useState } from "react";
import { motion } from "motion/react";
import {
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
  Map as MapIcon,
  MapPin,
  MoreHorizontal,
  PanelLeftClose,
  Plus,
  Route,
  Search,
  Settings2,
  Sparkles,
} from "lucide-react";
import { Timeline } from "./timeline";

const MapCanvas = dynamic(() => import("./map-canvas").then((module) => module.MapCanvas), {
  ssr: false,
  loading: () => <div className="map-loading">正在准备地图画布…</div>,
});

const layers = [
  { id: "track", label: "移动轨迹", icon: Route, color: "mint", visible: true },
  { id: "stays", label: "停留地点", icon: MapPin, color: "amber", visible: true },
  { id: "heat", label: "足迹热力", icon: Sparkles, color: "violet", visible: false },
];

export function WorkspaceShell() {
  const [visibleLayers, setVisibleLayers] = useState(() => new Set(["track", "stays"]));
  const [activeTool, setActiveTool] = useState("layers");

  function toggleLayer(id: string) {
    setVisibleLayers((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <main className="workspace">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark"><Route size={17} /></div>
          <span>RouteFrom</span>
          <em>探索</em>
        </div>

        <button className="view-switcher">
          <span className="status-dot" />
          上海 · 七月漫游
          <ChevronDown size={14} />
        </button>

        <div className="topbar-center">
          <button className="date-control">
            <CalendarRange size={15} />
            <span>2026.07.21 — 07.28</span>
            <ChevronDown size={13} />
          </button>
          <button className="command-search">
            <Search size={15} />
            <span>搜索地点、日期或行程</span>
            <kbd><Command size={11} /> K</kbd>
          </button>
        </div>

        <div className="topbar-actions">
          <span className="sync-state"><Cloud size={14} />本地草稿</span>
          <button className="icon-button"><CircleHelp size={16} /></button>
          <button className="avatar">RF</button>
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
          >
            <Icon size={18} />
          </button>
        ))}
        <button className="rail-bottom" aria-label="收起侧栏"><PanelLeftClose size={18} /></button>
      </aside>

      <motion.aside
        className="layer-panel"
        initial={{ opacity: 0, x: -8 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ duration: 0.35, ease: "easeOut" }}
      >
        <div className="panel-heading">
          <div><span>当前视图</span><h1>足迹图层</h1></div>
          <button className="icon-button"><MoreHorizontal size={17} /></button>
        </div>

        <div className="dataset-card">
          <div className="dataset-icon"><Database size={16} /></div>
          <div><strong>灵敢足迹</strong><span>2023.04 — 2026.08</span></div>
          <span className="ready-badge"><Check size={11} />就绪</span>
        </div>

        <section className="panel-section">
          <div className="section-title"><span>可视图层</span><button><Plus size={14} />添加</button></div>
          <div className="layer-list">
            {layers.map(({ id, label, icon: Icon, color }) => {
              const visible = visibleLayers.has(id);
              return (
                <button key={id} className={`layer-item ${visible ? "is-visible" : ""}`} onClick={() => toggleLayer(id)}>
                  <span className={`layer-icon ${color}`}><Icon size={15} /></span>
                  <span className="layer-name">{label}<small>{id === "track" ? "186.4 公里" : id === "stays" ? "24 个地点" : "未显示"}</small></span>
                  {visible ? <Eye size={15} /> : <EyeOff size={15} />}
                </button>
              );
            })}
          </div>
        </section>

        <section className="panel-section slices-section">
          <div className="section-title"><span>时间切片</span><button><Plus size={14} /></button></div>
          <button className="slice-card active">
            <span className="slice-color mint" />
            <span><strong>七月漫游</strong><small>7 月 21 日 — 28 日</small></span>
            <Check size={14} />
          </button>
          <button className="slice-card">
            <span className="slice-color blue" />
            <span><strong>春季周末</strong><small>4 个不连续区间</small></span>
          </button>
        </section>

        <button className="new-view-button"><Plus size={15} />创建时间切片</button>
      </motion.aside>

      <section className="map-stage">
        <MapCanvas />
        <div className="map-vignette" />
        <div className="map-toolbar">
          <button className="active"><Focus size={16} />聚焦轨迹</button>
          <button><Settings2 size={16} /></button>
        </div>
        <div className="map-stat">
          <span>当前视图</span>
          <strong>186.4 <small>km</small></strong>
          <div><span>移动 8h 42m</span><span>停留 24 处</span></div>
        </div>
      </section>

      <motion.aside
        className="inspector"
        initial={{ opacity: 0, x: 8 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ duration: 0.35, ease: "easeOut", delay: 0.06 }}
      >
        <div className="panel-heading compact">
          <div><span>已选择</span><h2>陆家嘴</h2></div>
          <button className="icon-button"><MoreHorizontal size={17} /></button>
        </div>

        <div className="place-preview">
          <div className="place-orbit"><MapPin size={19} /></div>
          <span>31.2040° N</span><span>121.5090° E</span>
        </div>

        <div className="metric-grid">
          <div><span>累计停留</span><strong>4h 26m</strong></div>
          <div><span>访问次数</span><strong>7</strong></div>
          <div><span>首次到访</span><strong>07.21</strong></div>
          <div><span>最近到访</span><strong>07.27</strong></div>
        </div>

        <section className="inspector-section">
          <span className="eyebrow">最近活动</span>
          <div className="activity-list">
            <div><i /><span><strong>停留 38 分钟</strong><small>7 月 27 日 · 18:42</small></span></div>
            <div><i /><span><strong>地铁抵达</strong><small>14.8 公里 · 42 分钟</small></span></div>
            <div><i /><span><strong>停留 1 小时 12 分</strong><small>7 月 24 日 · 20:16</small></span></div>
          </div>
        </section>

        <button className="detail-button">查看地点详情</button>
      </motion.aside>

      <Timeline />
    </main>
  );
}
