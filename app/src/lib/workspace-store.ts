"use client";

import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

import { normalizeTimeRanges, type TimeRange } from "./workspace-query";
import type { WorkspacePreview } from "./workspace-data";

export type LayerId = "track" | "stays" | "gaps" | "places";
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
  ranges: TimeRange[];
  visibleLayers: Record<LayerId, boolean>;
  selection: { kind: SelectionKind; id: string } | null;
  cursorTime: string | null;
  mapView: MapView;
  setLoading: () => void;
  setData: (data: WorkspacePreview) => void;
  setError: (message: string) => void;
  toggleLayer: (layer: LayerId) => void;
  addRange: () => void;
  updateRange: (index: number, range: TimeRange) => void;
  removeRange: (index: number) => void;
  useSuggestedRange: () => void;
  setSelection: (selection: WorkspaceState["selection"]) => void;
  setCursorTime: (instant: string | null) => void;
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
      ranges: [],
      visibleLayers: { track: true, stays: true, gaps: true, places: true },
      selection: null,
      cursorTime: null,
      mapView: initialMapView,
      setLoading: () => set({ status: "loading", error: null }),
      setData: (data) =>
        set((state) => ({
          data,
          status: "ready",
          error: null,
          ranges: state.ranges.length > 0 ? state.ranges : data.suggestedRanges,
          cursorTime: state.cursorTime ?? data.suggestedRanges[0]?.start ?? null,
        })),
      setError: (message) => set({ status: "error", error: message }),
      toggleLayer: (layer) =>
        set((state) => ({
          visibleLayers: {
            ...state.visibleLayers,
            [layer]: !state.visibleLayers[layer],
          },
        })),
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
        if (data) set({ ranges: data.suggestedRanges, cursorTime: data.suggestedRanges[0]?.start });
      },
      setSelection: (selection) => set({ selection }),
      setCursorTime: (cursorTime) => set({ cursorTime }),
      setMapView: (mapView) => set({ mapView }),
    }),
    {
      name: "routefrom-workspace-view-v1",
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        ranges: state.ranges,
        visibleLayers: state.visibleLayers,
        mapView: state.mapView,
      }),
    },
  ),
);
