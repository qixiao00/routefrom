"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Pause, Play, SkipBack, SkipForward } from "lucide-react";

import { overlapsSelection, type WorkspacePreview } from "@/lib/workspace-data";
import type { SelectionKind } from "@/lib/workspace-store";
import type { TimeRange } from "@/lib/workspace-query";

interface TimelineProps {
  data: WorkspacePreview | null;
  ranges: TimeRange[];
  cursorTime: string | null;
  selection: { kind: SelectionKind; id: string } | null;
  onCursorChange: (instant: string | null) => void;
  onSelect: (selection: { kind: SelectionKind; id: string } | null) => void;
}

const MODE_COLORS: Record<string, string> = {
  walk: "#8dd8c4",
  run: "#67c7ae",
  bicycle: "#72a7ff",
  e_bike: "#72a7ff",
  car: "#a89be5",
  bus: "#d79fc6",
  motorcycle: "#a89be5",
  scooter: "#8caee2",
  metro: "#db8f7b",
  train: "#e3a179",
  unknown: "#697571",
};

function formatInstant(instant: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(instant));
}

export function Timeline({
  data,
  ranges,
  cursorTime,
  selection,
  onCursorChange,
  onSelect,
}: TimelineProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const cursorRef = useRef(cursorTime);
  const [playing, setPlaying] = useState(false);
  const domain = useMemo(() => {
    if (ranges.length === 0) return null;
    return {
      start: Math.min(...ranges.map((range) => Date.parse(range.start))),
      end: Math.max(...ranges.map((range) => Date.parse(range.end))),
    };
  }, [ranges]);
  const visibleLegs = useMemo(
    () => data?.modeLegs.filter((leg) => overlapsSelection(leg.start, leg.end, ranges)) ?? [],
    [data, ranges],
  );
  const visibleStays = useMemo(
    () => data?.stays.filter((stay) => overlapsSelection(stay.start, stay.end, ranges)) ?? [],
    [data, ranges],
  );
  const visibleGaps = useMemo(
    () => data?.gaps.filter((gap) => overlapsSelection(gap.start, gap.end, ranges)) ?? [],
    [data, ranges],
  );

  useEffect(() => {
    cursorRef.current = cursorTime;
  }, [cursorTime]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !domain) return;
    const observer = new ResizeObserver(() => draw());
    observer.observe(canvas);

    function draw() {
      if (!canvas || !domain) return;
      const bounds = canvas.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.max(1, Math.round(bounds.width * dpr));
      canvas.height = Math.max(1, Math.round(bounds.height * dpr));
      const context = canvas.getContext("2d");
      if (!context) return;
      context.scale(dpr, dpr);
      context.clearRect(0, 0, bounds.width, bounds.height);
      const left = 18;
      const right = bounds.width - 18;
      const width = Math.max(1, right - left);
      const x = (instant: string | number) => {
        const time = typeof instant === "number" ? instant : Date.parse(instant);
        return left + ((time - domain.start) / Math.max(1, domain.end - domain.start)) * width;
      };

      context.fillStyle = "rgba(255,255,255,0.025)";
      context.fillRect(left, 7, width, 104);
      for (let index = 0; index <= 8; index += 1) {
        const gridX = left + (width * index) / 8;
        context.strokeStyle = "rgba(220,228,224,0.06)";
        context.beginPath();
        context.moveTo(gridX, 7);
        context.lineTo(gridX, 111);
        context.stroke();
      }

      ranges.forEach((range, index) => {
        const start = x(range.start);
        const end = x(range.end);
        context.fillStyle = index % 2 === 0 ? "rgba(141,216,196,0.16)" : "rgba(114,167,255,0.16)";
        context.fillRect(start, 7, Math.max(2, end - start), 104);
        context.fillStyle = index % 2 === 0 ? "#8dd8c4" : "#72a7ff";
        context.fillRect(start, 7, Math.max(2, end - start), 2);
      });

      visibleLegs.forEach((leg) => {
        context.fillStyle = MODE_COLORS[leg.mode] ?? MODE_COLORS.unknown;
        context.globalAlpha = 0.35 + 0.6 * leg.confidence;
        context.fillRect(x(leg.start), 28, Math.max(1.5, x(leg.end) - x(leg.start)), 16);
      });
      context.globalAlpha = 1;

      visibleStays.forEach((stay) => {
        context.fillStyle = stay.kind === "visit" ? "#d9a657" : stay.kind === "transport_pause" ? "#89938f" : "#65706c";
        context.globalAlpha = selection?.kind === "stay" && selection.id === stay.id ? 1 : 0.72;
        context.fillRect(x(stay.start), 57, Math.max(2, x(stay.end) - x(stay.start)), 14);
      });
      context.globalAlpha = 1;

      visibleGaps.forEach((gap) => {
        const start = x(gap.start);
        const end = x(gap.end);
        context.strokeStyle = selection?.kind === "gap" && selection.id === gap.id ? "#f0f3f1" : "#7d8884";
        context.setLineDash([4, 4]);
        context.beginPath();
        context.moveTo(start, 91);
        context.lineTo(end, 91);
        context.stroke();
      });
      context.setLineDash([]);

      if (cursorTime) {
        const cursorX = x(cursorTime);
        context.strokeStyle = "rgba(243,247,245,0.86)";
        context.lineWidth = 1;
        context.beginPath();
        context.moveTo(cursorX + 0.5, 2);
        context.lineTo(cursorX + 0.5, 116);
        context.stroke();
        context.fillStyle = "#f3f7f5";
        context.beginPath();
        context.arc(cursorX, 3, 2.5, 0, Math.PI * 2);
        context.fill();
      }
    }

    draw();
    return () => observer.disconnect();
  }, [cursorTime, domain, ranges, selection, visibleGaps, visibleLegs, visibleStays]);

  useEffect(() => {
    if (!playing || ranges.length === 0) return;
    let frame = 0;
    let previous = performance.now();
    const totalSelected = ranges.reduce(
      (sum, range) => sum + Date.parse(range.end) - Date.parse(range.start),
      0,
    );
    function tick(now: number) {
      const elapsed = now - previous;
      previous = now;
      const current = cursorRef.current
        ? Date.parse(cursorRef.current)
        : Date.parse(ranges[0].start);
      const rangeIndex = Math.max(0, ranges.findIndex((range) => current >= Date.parse(range.start) && current < Date.parse(range.end)));
      const range = ranges[rangeIndex];
      const next = current + (totalSelected / 30_000) * elapsed;
      if (next >= Date.parse(range.end)) {
        const following = ranges[rangeIndex + 1];
        if (following) {
          cursorRef.current = following.start;
          onCursorChange(following.start);
        }
        else {
          cursorRef.current = ranges[0].start;
          onCursorChange(ranges[0].start);
          setPlaying(false);
          return;
        }
      } else {
        const nextInstant = new Date(next).toISOString();
        cursorRef.current = nextInstant;
        onCursorChange(nextInstant);
      }
      frame = requestAnimationFrame(tick);
    }
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [onCursorChange, playing, ranges]);

  function seek(event: React.MouseEvent<HTMLCanvasElement>) {
    if (!domain) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const fraction = Math.min(1, Math.max(0, (event.clientX - bounds.left - 18) / Math.max(1, bounds.width - 36)));
    const instant = domain.start + fraction * (domain.end - domain.start);
    onCursorChange(new Date(instant).toISOString());
    const stay = visibleStays.find((item) => instant >= Date.parse(item.start) && instant < Date.parse(item.end));
    const gap = visibleGaps.find((item) => instant >= Date.parse(item.start) && instant < Date.parse(item.end));
    onSelect(stay ? { kind: "stay", id: stay.id } : gap ? { kind: "gap", id: gap.id } : null);
  }

  function jump(direction: -1 | 1) {
    if (ranges.length === 0) return;
    const current = cursorTime ? Date.parse(cursorTime) : Date.parse(ranges[0].start);
    const ordered = direction > 0 ? ranges : [...ranges].reverse();
    const target = ordered.find((range) => direction > 0 ? Date.parse(range.start) > current : Date.parse(range.end) < current);
    onCursorChange(target ? (direction > 0 ? target.start : target.end) : direction > 0 ? ranges[0].start : ranges.at(-1)!.end);
  }

  return (
    <section className="timeline" aria-label="足迹时间轴">
      <div className="timeline-toolbar">
        <div className="timeline-title">
          <span>时间地层</span>
          <strong>{ranges.length === 0 ? "尚未选择" : ranges.length === 1 ? `${formatInstant(ranges[0].start)} — ${formatInstant(ranges[0].end)}` : `${ranges.length} 个不连续区间`}</strong>
        </div>
        <div className="playback-controls">
          <button type="button" aria-label="上一段" onClick={() => jump(-1)}><SkipBack size={14} /></button>
          <button type="button" className="play-button" aria-label={playing ? "暂停" : "播放"} onClick={() => setPlaying((value) => !value)}>{playing ? <Pause size={14} /> : <Play size={14} />}</button>
          <button type="button" aria-label="下一段" onClick={() => jump(1)}><SkipForward size={14} /></button>
        </div>
        <div className="timeline-summary"><span />{visibleLegs.length} 个移动段 · {visibleGaps.length} 个未知缺口</div>
      </div>

      <div className="timeline-chart temporal-strata">
        <div className="timeline-labels"><span>移动</span><span>静止</span><span>未知</span></div>
        <div className="timeline-plot">
          <canvas ref={canvasRef} onClick={seek} aria-label="点击设置播放时间" />
          {cursorTime && <div className="timeline-cursor-label">{formatInstant(cursorTime)}</div>}
          <div className="timeline-legend"><span className="observed">已观测</span><span className="stationary">静止</span><span className="unknown">未知时间</span></div>
        </div>
      </div>
    </section>
  );
}
