"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";
import type { FeatureCollection, LineString, MultiLineString, Point } from "geojson";
import type { MapLayerMouseEvent, MapRef } from "react-map-gl/maplibre";
import Map, { Layer, NavigationControl, Source } from "react-map-gl/maplibre";

import type {
  PreviewGap,
  PreviewStay,
  SelectedPath,
  WorkspacePreview,
} from "@/lib/workspace-data";
import type { LayerId, MapView, SelectionKind } from "@/lib/workspace-store";
import type { ViewportBounds } from "@/lib/workspace-viewport";

interface MapCanvasProps {
  data: WorkspacePreview;
  selectedPaths: SelectedPath[];
  selectedBounds: ViewportBounds | null;
  selectedStays: PreviewStay[];
  selectedGaps: PreviewGap[];
  visibleLayers: Record<LayerId, boolean>;
  selection: { kind: SelectionKind; id: string } | null;
  mapView: MapView;
  fitRequest: number;
  onMapViewChange: (view: MapView) => void;
  onViewportChange: (bounds: ViewportBounds) => void;
  onSelect: (selection: { kind: SelectionKind; id: string } | null) => void;
}

const baseStyle = {
  version: 8 as const,
  sources: {
    "osm-raster": {
      type: "raster" as const,
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors",
      maxzoom: 19,
    },
  },
  layers: [
    { id: "background", type: "background" as const, paint: { "background-color": "#080d0f" } },
    {
      id: "osm-raster-layer",
      type: "raster" as const,
      source: "osm-raster",
      paint: {
        "raster-opacity": 0.27,
        "raster-saturation": -0.88,
        "raster-contrast": 0.28,
        "raster-brightness-min": 0.03,
        "raster-brightness-max": 0.38,
      },
    },
  ],
};

export function MapCanvas({
  data,
  selectedPaths,
  selectedBounds,
  selectedStays,
  selectedGaps,
  visibleLayers,
  selection,
  mapView,
  fitRequest,
  onMapViewChange,
  onViewportChange,
  onSelect,
}: MapCanvasProps) {
  const mapRef = useRef<MapRef>(null);
  const handledFitRequest = useRef(0);
  const lineData = useMemo<FeatureCollection<MultiLineString>>(() => {
    const byRange = new globalThis.Map<number, number[][][]>();
    for (const path of selectedPaths) {
      if (path.vertices.length < 2) continue;
      const pieces = byRange.get(path.rangeIndex) ?? [];
      pieces.push(path.vertices.map((vertex) => [vertex[1], vertex[2]]));
      byRange.set(path.rangeIndex, pieces);
    }
    return {
      type: "FeatureCollection",
      features: [...byRange].map(([rangeIndex, coordinates]) => ({
        type: "Feature",
        properties: { paletteIndex: rangeIndex % 2 },
        geometry: { type: "MultiLineString", coordinates },
      })),
    };
  }, [selectedPaths]);
  const gapData = useMemo<FeatureCollection<LineString>>(
    () => ({
      type: "FeatureCollection",
      features: selectedGaps.map((gap) => ({
        type: "Feature",
        properties: { id: gap.id, kind: "gap", confidence: gap.confidence },
        geometry: { type: "LineString", coordinates: [gap.startPosition, gap.endPosition] },
      })),
    }),
    [selectedGaps],
  );
  const stayData = useMemo<FeatureCollection<Point>>(
    () => ({
      type: "FeatureCollection",
      features: selectedStays.map((stay) => ({
        type: "Feature",
        properties: {
          id: stay.id,
          kind: "stay",
          eventKind: stay.kind,
          confidence: stay.confidence,
          selected: selection?.kind === "stay" && selection.id === stay.id,
        },
        geometry: { type: "Point", coordinates: stay.position },
      })),
    }),
    [selectedStays, selection],
  );
  const placeData = useMemo<FeatureCollection<Point>>(
    () => ({
      type: "FeatureCollection",
      features: data.places.map((place) => ({
        type: "Feature",
        properties: {
          id: place.id,
          kind: "place",
          name: place.name,
          frequency: place.frequentProbability,
          selected: selection?.kind === "place" && selection.id === place.id,
        },
        geometry: { type: "Point", coordinates: place.position },
      })),
    }),
    [data.places, selection],
  );

  const fitSelection = useCallback(() => {
    if (!mapRef.current || !selectedBounds) return;
    mapRef.current.fitBounds(
      [
        [selectedBounds[0], selectedBounds[1]],
        [selectedBounds[2], selectedBounds[3]],
      ],
      { padding: { top: 80, right: 80, bottom: 120, left: 80 }, duration: 850, maxZoom: 14 },
    );
  }, [selectedBounds]);

  const reportViewport = useCallback(() => {
    const map = mapRef.current?.getMap();
    if (!map) return;
    const bounds = map.getBounds();
    const west = Math.max(-180, bounds.getWest());
    const south = Math.max(-90, bounds.getSouth());
    const east = Math.min(180, bounds.getEast());
    const north = Math.min(90, bounds.getNorth());
    onViewportChange(west < east && south < north
      ? [west, south, east, north]
      : [-180, -90, 180, 90]);
  }, [onViewportChange]);

  useEffect(() => {
    if (fitRequest === 0 || fitRequest === handledFitRequest.current || !selectedBounds) return;
    handledFitRequest.current = fitRequest;
    const frame = requestAnimationFrame(fitSelection);
    return () => {
      cancelAnimationFrame(frame);
    };
  }, [fitRequest, fitSelection, selectedBounds]);

  useEffect(() => {
    const coordinate = selection?.kind === "stay"
      ? data.stays.find((item) => item.id === selection.id)?.position
      : selection?.kind === "place"
        ? data.places.find((item) => item.id === selection.id)?.position
        : null;
    if (coordinate) mapRef.current?.flyTo({ center: coordinate, zoom: 14, duration: 650 });
  }, [data.places, data.stays, selection]);

  function handleClick(event: MapLayerMouseEvent) {
    const feature = event.features?.[0];
    const kind = feature?.properties?.kind as SelectionKind | undefined;
    const id = feature?.properties?.id as string | undefined;
    onSelect(kind && id ? { kind, id } : null);
  }

  return (
    <Map
      ref={mapRef}
      initialViewState={mapView}
      mapStyle={baseStyle}
      attributionControl={{ compact: true }}
      reuseMaps
      interactiveLayerIds={["stay-points", "gap-lines", "place-points"]}
      onClick={handleClick}
      onLoad={reportViewport}
      onResize={reportViewport}
      onMoveEnd={(event) => {
        onMapViewChange({
          longitude: event.viewState.longitude,
          latitude: event.viewState.latitude,
          zoom: event.viewState.zoom,
          pitch: event.viewState.pitch,
          bearing: event.viewState.bearing,
        });
        reportViewport();
      }}
      cursor="default"
    >
      <NavigationControl position="bottom-right" visualizePitch />

      <Source id="observed-tracks" type="geojson" data={lineData}>
        <Layer id="track-lines" type="line" layout={{ "line-cap": "round", "line-join": "round" }} paint={{ "line-color": ["case", ["==", ["get", "paletteIndex"], 0], "#9be2cf", "#7dafef"], "line-width": ["interpolate", ["linear"], ["zoom"], 4, 1, 10, 1.4, 15, 2.2], "line-opacity": visibleLayers.track ? 0.82 : 0 }} />
      </Source>

      <Source id="unknown-gaps" type="geojson" data={gapData}>
        <Layer id="gap-lines" type="line" layout={{ "line-cap": "round" }} paint={{ "line-color": "#a3aea9", "line-width": 1.5, "line-opacity": visibleLayers.gaps ? 0.62 : 0, "line-dasharray": [2, 2.4] }} />
      </Source>

      <Source id="stationary-events" type="geojson" data={stayData}>
        <Layer id="stay-points" type="circle" paint={{ "circle-radius": ["case", ["get", "selected"], 8, ["==", ["get", "eventKind"], "visit"], 5, 3.5], "circle-color": ["case", ["==", ["get", "eventKind"], "visit"], "#d9a657", ["==", ["get", "eventKind"], "transport_pause"], "#9da7a3", "#6f7a76"], "circle-opacity": visibleLayers.stays ? 0.9 : 0, "circle-stroke-width": ["case", ["get", "selected"], 3, 1.2], "circle-stroke-color": "#f5ead7" }} />
      </Source>

      <Source id="frequent-places" type="geojson" data={placeData}>
        <Layer id="place-points" type="circle" paint={{ "circle-radius": ["interpolate", ["linear"], ["get", "frequency"], 0, 5, 1, 11], "circle-color": "#72a7ff", "circle-opacity": visibleLayers.places ? 0.2 : 0, "circle-stroke-width": ["case", ["get", "selected"], 2.5, 1], "circle-stroke-color": "#a8c7ff" }} />
        <Layer id="place-labels" type="symbol" minzoom={10} layout={{ "text-field": ["get", "name"], "text-size": 11, "text-offset": [0, 1.4], "text-anchor": "top", "text-allow-overlap": false }} paint={{ "text-color": "#c8d7e9", "text-halo-color": "#0a0e10", "text-halo-width": 1.5, "text-opacity": visibleLayers.places ? 0.78 : 0 }} />
      </Source>
    </Map>
  );
}
