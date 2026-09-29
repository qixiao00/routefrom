import type { FeatureCollection, MultiLineString } from "geojson";
import type { ViewportPath } from "./workspace-viewport.ts";

type XY = [number, number];
interface Corridor {
  a: XY; b: XY; length: number; ux: number; uy: number; palette: number;
  intervals: { start: number; end: number; pass: string }[];
}
const mix = (a: XY, b: XY, t: number): XY => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];

/** Display-only corridor overlap. Never feeds distance, events or processing results. */
export function aggregateOrdinaryRoutes(paths: readonly ViewportPath[], zoom: number): FeatureCollection<MultiLineString> {
  const ordinary = paths.filter(p => p.movementClass === "ordinary" && p.vertices.length > 1);
  const origin = ordinary[0]?.vertices[0];
  if (!origin) return { type: "FeatureCollection", features: [] };
  const sx = 111320 * Math.max(0.01, Math.cos(origin[2] * Math.PI / 180));
  const sy = 111320;
  const project = (lon: number, lat: number): XY => [(lon - origin[1]) * sx, (lat - origin[2]) * sy];
  const unproject = (p: XY): XY => [Number((origin[1] + p[0] / sx).toFixed(8)), Number((origin[2] + p[1] / sy).toFixed(8))];
  // Display-only: lines inside ~2.5 screen pixels read as one stroke. Keep
  // the cap conservative so nearby parallel streets separate as users zoom in.
  const tolerance = Math.max(0.5, Math.min(25, 2.5 * 156543.03 * sx / 111320 / 2 ** zoom));
  const cell = 100;
  const corridors: Corridor[] = [];
  const grid = new Map<string, number[]>();
  const key = (x: number, y: number) => `${x},${y}`;
  function insert(a: XY, b: XY, segment: number, palette: number) {
    const length = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (length < 0.01) return;
    const ux = (b[0] - a[0]) / length, uy = (b[1] - a[1]) / length;
    const mid = mix(a, b, 0.5);
    const gx = Math.floor(mid[0] / cell), gy = Math.floor(mid[1] / cell);
    let remaining: [number, number][] = [[0, 1]];
    const candidates: { id: number; distance: number }[] = [];
    for (let x = gx - 1; x <= gx + 1; x++) for (let y = gy - 1; y <= gy + 1; y++) {
      for (const id of grid.get(key(x, y)) ?? []) {
        const c = corridors[id];
        if (Math.abs(ux * c.ux + uy * c.uy) < 0.9962) continue; // five degrees, both directions
        const da = Math.abs((a[0] - c.a[0]) * c.uy - (a[1] - c.a[1]) * c.ux);
        const db = Math.abs((b[0] - c.a[0]) * c.uy - (b[1] - c.a[1]) * c.ux);
        if (Math.max(da, db) <= tolerance) candidates.push({ id, distance: Math.max(da, db) });
      }
    }
    candidates.sort((a, b) => a.distance - b.distance || a.id - b.id);
    for (const { id } of candidates) {
      const c = corridors[id];
      const start = (a[0] - c.a[0]) * c.ux + (a[1] - c.a[1]) * c.uy;
      const delta = length * (ux * c.ux + uy * c.uy);
      const t0 = (0 - start) / delta, t1 = (c.length - start) / delta;
      const lo = Math.max(0, Math.min(t0, t1)), hi = Math.min(1, Math.max(t0, t1));
      if (hi <= lo) continue;
      const next: [number, number][] = [];
      for (const [l, h] of remaining) {
        const from = Math.max(l, lo), to = Math.min(h, hi);
        if ((to - from) * length < 0.01) { next.push([l, h]); continue; }
        const p = start + delta * from, q = start + delta * to;
        c.intervals.push({ start: Math.max(0, Math.min(p, q)), end: Math.min(c.length, Math.max(p, q)), pass: `${segment}:${delta > 0 ? 1 : -1}` });
        if (from > l) next.push([l, from]);
        if (to < h) next.push([to, h]);
      }
      remaining = next;
      if (!remaining.length) break;
    }
    for (const [lo, hi] of remaining) {
      const p = mix(a, b, lo), q = mix(a, b, hi), size = (hi - lo) * length;
      if (size < 0.01) continue;
      const id = corridors.length;
      corridors.push({ a: p, b: q, length: size, ux, uy, palette,
        intervals: [{ start: 0, end: size, pass: `${segment}:1` }] });
      const m = mix(p, q, 0.5), k = key(Math.floor(m[0] / cell), Math.floor(m[1] / cell));
      const ids = grid.get(k) ?? []; ids.push(id); grid.set(k, ids);
    }
  }
  for (const path of ordinary) for (let i = 1; i < path.vertices.length; i++) {
    const av = path.vertices[i - 1], bv = path.vertices[i];
    const a = project(av[1], av[2]), b = project(bv[1], bv[2]);
    // Local pieces bound index fan-out even for simplified long edges.
    const parts = Math.max(1, Math.ceil(Math.hypot(b[0] - a[0], b[1] - a[1]) / 80));
    for (let j = 0; j < parts; j++) insert(mix(a, b, j / parts), mix(a, b, (j + 1) / parts), path.segmentIndex, path.rangeIndex % 2);
  }
  const groups = new Map<string, { count: number; palette: number; lines: number[][][] }>();
  for (const c of corridors) {
    const events = c.intervals.flatMap(v => [{ at: v.start, pass: v.pass, delta: 1 }, { at: v.end, pass: v.pass, delta: -1 }]).sort((a, b) => a.at - b.at);
    const active = new Map<string, number>();
    let last = 0;
    const runs: { start: number; end: number; count: number }[] = [];
    for (const event of events) {
      if (event.at - last > 0.01 && active.size) {
        // Width bands avoid a new line fragment for every tiny frequency change.
        const n = active.size;
        const count = n < 3 ? n : n < 5 ? 3 : n < 10 ? 5 : n < 20 ? 10 : 20;
        const previous = runs.at(-1);
        if (previous?.count === count && last - previous.end < 0.01) previous.end = event.at;
        else runs.push({ start: last, end: event.at, count });
      }
      const n = (active.get(event.pass) ?? 0) + event.delta;
      if (n === 0) active.delete(event.pass); else active.set(event.pass, n);
      last = event.at;
    }
    for (const run of runs) {
      const count = run.count, palette = count > 1 ? 0 : c.palette;
      const k = `${count}:${palette}`, group = groups.get(k) ?? { count, palette, lines: [] };
      group.lines.push([unproject(mix(c.a, c.b, run.start / c.length)), unproject(mix(c.a, c.b, run.end / c.length))]);
      groups.set(k, group);
    }
  }
  return { type: "FeatureCollection", features: [...groups.values()].map(g => ({
    type: "Feature", properties: { movementClass: "ordinary", paletteIndex: g.palette, traversalCount: g.count },
    geometry: { type: "MultiLineString", coordinates: stitchLines(g.lines, sx, sy) },
  })) };
}

// Join degree-two endpoints only; intersections/branches and different width
// bands stay separate. Sub-centimeter rounding is solely for floating point keys.
function stitchLines(lines: number[][][], sx: number, sy: number): number[][][] {
  const pointKey = (p: number[]) => `${p[0].toFixed(7)},${p[1].toFixed(7)}`;
  const adjacent = new Map<string, number[]>();
  lines.forEach((line, id) => line.forEach(p => {
    const key = pointKey(p), ids = adjacent.get(key) ?? [];
    ids.push(id); adjacent.set(key, ids);
  }));
  const used = new Set<number>(), output: number[][][] = [];
  function walk(id: number, start: number[]) {
    const points = [start];
    let from = pointKey(start);
    while (!used.has(id)) {
      used.add(id);
      const line = lines[id], end = pointKey(line[0]) === from ? line[1] : line[0];
      // Remove only collinear subdivisions, never a bend or a gap.
      if (points.length >= 2) {
        const a = points[points.length - 2], b = points[points.length - 1];
        const dx = (b[0] - a[0]) * sx, dy = (b[1] - a[1]) * sy;
        const ex = (end[0] - b[0]) * sx, ey = (end[1] - b[1]) * sy;
        if (dx * ex + dy * ey > 0 && Math.abs(dx * ey - dy * ex) / Math.max(0.01, Math.hypot(dx + ex, dy + ey)) < 0.01) points.pop();
      }
      points.push(end);
      from = pointKey(end);
      const next = adjacent.get(from)!;
      if (next.length !== 2) break;
      const candidate = next.find(n => !used.has(n));
      if (candidate === undefined) break;
      id = candidate;
    }
    output.push(points);
  }
  lines.forEach((line, id) => {
    const start = line.find(p => adjacent.get(pointKey(p))!.length !== 2);
    if (start && !used.has(id)) walk(id, start);
  });
  lines.forEach((line, id) => { if (!used.has(id)) walk(id, line[0]); });
  return output;
}
