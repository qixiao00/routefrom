import { DEFAULT_LAYERS, DEFAULT_MAP_VIEW, DEFAULT_PANELS } from "./workspace-session.ts";
import { normalizeTimeRanges, type TimeRange } from "./workspace-query.ts";

export interface MapViewConfig {
  schemaVersion: 1;
  map: {
    projection: "globe";
    style: "openfreemap-dark-v1";
    terrain: false;
    buildings: false;
    camera: typeof DEFAULT_MAP_VIEW;
  };
  ranges: TimeRange[];
  visibleLayers: typeof DEFAULT_LAYERS;
  panels: typeof DEFAULT_PANELS;
}

export interface SavedMapView { id: string; title: string; config: MapViewConfig; createdAt: string }
export interface MapViewCollection {
  revision: number;
  config: MapViewConfig | null;
  updatedAt: string | null;
  views: SavedMapView[];
}

export function canonicalMapCamera(camera: typeof DEFAULT_MAP_VIEW) {
  const wrap = (angle: number) => ((angle + 180) % 360 + 360) % 360 - 180;
  return { ...camera, longitude: wrap(camera.longitude), bearing: wrap(camera.bearing) };
}

export function mapCamerasMatch(left: typeof DEFAULT_MAP_VIEW, right: typeof DEFAULT_MAP_VIEW): boolean {
  const a = canonicalMapCamera(left), b = canonicalMapCamera(right);
  const distance = (x: number, y: number) => Math.min(Math.abs(x - y), 360 - Math.abs(x - y));
  return distance(a.longitude, b.longitude) < 1e-6 && distance(a.bearing, b.bearing) < 1e-6 &&
    Math.abs(a.latitude - b.latitude) < 1e-6 && Math.abs(a.zoom - b.zoom) < 1e-6 && Math.abs(a.pitch - b.pitch) < 1e-6;
}

const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const exactKeys = (value: Record<string, unknown>, keys: string[]) =>
  Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));

// Strict on the wire: reject rather than silently drop unsupported/invalid data.
// This is the complete implemented map state, not generated events or notes.
export function parseMapViewConfig(value: unknown): MapViewConfig {
  const invalid = () => { throw new Error("地图配置不完整或版本不受支持。"); };
  if (!object(value) || !exactKeys(value, ["schemaVersion", "map", "ranges", "visibleLayers", "panels"]) || value.schemaVersion !== 1) return invalid();
  const map = value.map;
  if (!object(map) || !exactKeys(map, ["projection", "style", "terrain", "buildings", "camera"]) ||
    map.projection !== "globe" || map.style !== "openfreemap-dark-v1" || map.terrain !== false || map.buildings !== false) return invalid();
  const camera = map.camera;
  if (!object(camera) || !exactKeys(camera, Object.keys(DEFAULT_MAP_VIEW)) ||
    !Object.values(camera).every(item => typeof item === "number" && Number.isFinite(item)) ||
    Math.abs(Number(camera.longitude)) > 180 || Math.abs(Number(camera.latitude)) > 90 ||
    Number(camera.zoom) < 0 || Number(camera.zoom) > 24 || Number(camera.pitch) < 0 || Number(camera.pitch) > 85 ||
    Math.abs(Number(camera.bearing)) > 360) return invalid();
  const layers = value.visibleLayers;
  if (!object(layers) || !exactKeys(layers, Object.keys(DEFAULT_LAYERS)) || !Object.values(layers).every(item => typeof item === "boolean")) return invalid();
  const panels = value.panels;
  if (!object(panels) || !exactKeys(panels, Object.keys(DEFAULT_PANELS)) || !Object.values(panels).every(item => typeof item === "boolean")) return invalid();
  if (!Array.isArray(value.ranges) || value.ranges.length > 64) return invalid();
  const ranges: TimeRange[] = value.ranges.map(range => {
    if (!object(range) || !exactKeys(range, ["start", "end"]) || typeof range.start !== "string" || typeof range.end !== "string" ||
      !/(Z|[+-]\d{2}:\d{2})$/i.test(range.start) || !/(Z|[+-]\d{2}:\d{2})$/i.test(range.end) ||
      !Number.isFinite(Date.parse(range.start)) || !Number.isFinite(Date.parse(range.end)) || Date.parse(range.start) >= Date.parse(range.end)) return invalid();
    return { start: new Date(range.start).toISOString(), end: new Date(range.end).toISOString() };
  });
  return { schemaVersion: 1, map: { projection: "globe", style: "openfreemap-dark-v1", terrain: false, buildings: false,
    camera: { longitude: Number(camera.longitude), latitude: Number(camera.latitude), zoom: Number(camera.zoom), pitch: Number(camera.pitch), bearing: Number(camera.bearing) } },
    ranges: normalizeTimeRanges(ranges),
    visibleLayers: Object.fromEntries(Object.keys(DEFAULT_LAYERS).map(key => [key, layers[key]])) as typeof DEFAULT_LAYERS,
    panels: { layers: panels.layers as boolean, inspector: panels.inspector as boolean } };
}

export function serializeMapView(state: { mapView: typeof DEFAULT_MAP_VIEW; ranges: TimeRange[]; visibleLayers: typeof DEFAULT_LAYERS; panels: typeof DEFAULT_PANELS }): MapViewConfig {
  return parseMapViewConfig({ schemaVersion: 1, map: { projection: "globe", style: "openfreemap-dark-v1", terrain: false, buildings: false,
    camera: canonicalMapCamera(state.mapView) },
    ranges: state.ranges, visibleLayers: state.visibleLayers, panels: state.panels });
}
