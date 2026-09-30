"use client";

import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

import { normalizeTimeRanges, type TimeRange } from "./workspace-query";
import type { WorkspacePreview } from "./workspace-data";

export type LayerId = "track" | "sparse" | "highSpeed" | "stays" | "gaps" | "places";
export type SelectionKind = "stay" | "gap" | "trip" | "place" | "leg";

export interface MapView {
  longitude: number;
  latitude: number;
  zoom: number;
  pitch: number;
  bearing: number;
}

interface WorkspaceState {
  status: "idle" | "loading" | "ready" | "error";
  error: string | null;
  data: WorkspacePreview | null;
  viewRunId: string | null;
  ranges: TimeRange[];
  visibleLayers: Record<LayerId, boolean>;
  placeNotes: Record<string, string>;
  selection: { kind: SelectionKind; id: string } | null;
  mapView: MapView;
  setLoading: () => void;
  setData: (data: WorkspacePreview) => void;
  setError: (message: string) => void;
  toggleLayer: (layer: LayerId) => void;
  setPlaceNote: (key: string, note: string) => void;
  addRange: () => void;
  updateRange: (index: number, range: TimeRange) => void;
  removeRange: (index: number) => void;
  useSuggestedRange: () => void;
  setSelection: (selection: WorkspaceState["selection"]) => void;
  setMapView: (view: MapView) => void;
}

const initialMapView: MapView = {
  longitude: 112.98,
  latitude: 28.2,
  zoom: 10,
  pitch: 32,
  bearing: 0,
};

export const useWorkspaceStore = create<WorkspaceState>()(
  persist(
    (set, get) => ({
      status: "idle",
      error: null,
      data: null,
      viewRunId: null,
      ranges: [],
      visibleLayers: { track: true, sparse: true, highSpeed: false, stays: false, gaps: true, places: true },
      placeNotes: {},
      selection: null,
      mapView: initialMapView,
      setLoading: () => set({ status: "loading", error: null }),
      setData: (data) =>
        set((state) => {
          const sameRun = state.viewRunId === data.processing.runId;
          return {
            data,
            viewRunId: data.processing.runId,
            status: "ready",
            error: null,
            ranges: sameRun && state.ranges.length > 0 ? state.ranges : data.suggestedRanges,
            mapView: sameRun ? state.mapView : data.suggestedMapView ?? initialMapView,
            selection: sameRun ? state.selection : null,
          };
        }),
      setError: (message) => set({ status: "error", error: message }),
      toggleLayer: (layer) =>
        set((state) => ({
          visibleLayers: {
            ...state.visibleLayers,
            [layer]: !state.visibleLayers[layer],
          },
        })),
      setPlaceNote: (key, note) => set((state) => {
        const placeNotes = { ...state.placeNotes };
        const trimmed = note.trim().slice(0, 80);
        if (trimmed) placeNotes[key] = trimmed;
        else delete placeNotes[key];
        return { placeNotes };
      }),
      addRange: () => {
        const state = get();
        const anchor = state.ranges[0] ?? state.data?.suggestedRanges[0];
        if (!anchor) return;
        const duration = Math.max(60 * 60 * 1000, Date.parse(anchor.end) - Date.parse(anchor.start));
        const end = Date.parse(anchor.start) - Math.min(duration, 24 * 60 * 60 * 1000);
        const start = end - Math.min(duration, 3 * 24 * 60 * 60 * 1000);
        set({
          ranges: normalizeTimeRanges([
            ...state.ranges,
            { start: new Date(start).toISOString(), end: new Date(end).toISOString() },
          ]),
        });
      },
      updateRange: (index, range) => {
        if (Date.parse(range.start) >= Date.parse(range.end)) return;
        set((state) => ({
          ranges: normalizeTimeRanges(
            state.ranges.map((current, position) => (position === index ? range : current)),
          ),
        }));
      },
      removeRange: (index) =>
        set((state) => ({
          ranges: state.ranges.length <= 1
            ? state.ranges
            : state.ranges.filter((_, position) => position !== index),
        })),
      useSuggestedRange: () => {
        const data = get().data;
        if (data) set({ ranges: data.suggestedRanges });
      },
      setSelection: (selection) => set({ selection }),
      setMapView: (mapView) => set({ mapView }),
    }),
    {
      name: "routefrom-workspace-view-v1",
      version: 1,
      migrate: (persisted, version) => {
        const saved = persisted && typeof persisted === "object" ? persisted as Partial<WorkspaceState> : {};
        return version < 1
          ? { ...saved, visibleLayers: { ...saved.visibleLayers, stays: false } }
          : saved;
      },
      storage: createJSONStorage(() => localStorage),
      merge: (persisted, current) => {
        const saved = persisted && typeof persisted === "object"
          ? persisted as Partial<WorkspaceState>
          : {};
        return {
          ...current,
          ...saved,
          visibleLayers: {
            ...current.visibleLayers,
            ...(saved.visibleLayers && typeof saved.visibleLayers === "object" ? saved.visibleLayers : {}),
          },
          placeNotes: saved.placeNotes && typeof saved.placeNotes === "object" ? saved.placeNotes : {},
        };
      },
      partialize: (state) => ({
        viewRunId: state.viewRunId,
        ranges: state.ranges,
        visibleLayers: state.visibleLayers,
        placeNotes: state.placeNotes,
        mapView: state.mapView,
      }),
    },
  ),
);
