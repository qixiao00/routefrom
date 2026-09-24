"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";
import type { FeatureCollection, LineString, Point } from "geojson";
import type { MapLayerMouseEvent, MapRef } from "react-map-gl/maplibre";
import Map, { Layer, NavigationControl, Source } from "react-map-gl/maplibre";

import { simplifySelectedPath } from "@/lib/workspace-data";
import type {
  PreviewGap,
  PreviewStay,
  SelectedPath,
  WorkspacePreview,
} from "@/lib/workspace-data";
import type { LayerId, MapView, SelectionKind } from "@/lib/workspace-store";

interface MapCanvasProps {
  data: WorkspacePreview;
  selectedPaths: SelectedPath[];
  selectedStays: PreviewStay[];
  selectedGaps: PreviewGap[];
  visibleLayers: Record<LayerId, boolean>;
  selection: { kind: SelectionKind; id: string } | null;
  mapView: MapView;
  fitRequest: number;
  onMapViewChange: (view: MapView) => void;
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
  selectedStays,
  selectedGaps,
  visibleLayers,
  selection,
  mapView,
  fitRequest,
  onMapViewChange,
  onSelect,
}: MapCanvasProps) {
  const mapRef = useRef<MapRef>(null);
  const overlayRef = useRef<HTMLCanvasElement>(null);
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
    if (!mapRef.current) return;
    let minLongitude = Infinity;
    let minLatitude = Infinity;
    let maxLongitude = -Infinity;
    let maxLatitude = -Infinity;
    for (const path of selectedPaths) {
      for (const vertex of path.vertices) {
        minLongitude = Math.min(minLongitude, vertex[1]);
        maxLongitude = Math.max(maxLongitude, vertex[1]);
        minLatitude = Math.min(minLatitude, vertex[2]);
        maxLatitude = Math.max(maxLatitude, vertex[2]);
      }
    }
    if (!Number.isFinite(minLongitude)) return;
    mapRef.current.fitBounds(
      [
        [minLongitude, minLatitude],
        [maxLongitude, maxLatitude],
      ],
      { padding: { top: 80, right: 80, bottom: 120, left: 80 }, duration: 850, maxZoom: 14 },
    );
  }, [selectedPaths]);

  const drawOverlay = useCallback(() => {
    const map = mapRef.current?.getMap();
    const canvas = overlayRef.current;
    if (!map || !canvas) return;
    const bounds = map.getContainer().getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const width = Math.max(1, Math.round(bounds.width * dpr));
    const height = Math.max(1, Math.round(bounds.height * dpr));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    const context = canvas.getContext("2d");
    if (!context) return;
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.clearRect(0, 0, bounds.width, bounds.height);

    if (visibleLayers.track) {
      const zoom = map.getZoom();
      const latitude = map.getCenter().lat;
      const toleranceMeters =
        (156_543.03 * Math.cos((latitude * Math.PI) / 180) / 2 ** zoom) * 0.8;
      selectedPaths.forEach((path) => {
        if (path.vertices.length < 2) return;
        const vertices = simplifySelectedPath(path, toleranceMeters);
        context.beginPath();
        vertices.forEach((vertex, index) => {
          const point = map.project([vertex[1], vertex[2]]);
          if (index === 0) context.moveTo(point.x, point.y);
          else context.lineTo(point.x, point.y);
        });
        context.lineCap = "round";
        context.lineJoin = "round";
        context.strokeStyle = path.rangeIndex % 2 === 0
          ? (zoom < 9 ? "rgba(155,226,207,.65)" : "rgba(155,226,207,.88)")
          : (zoom < 9 ? "rgba(125,175,239,.65)" : "rgba(125,175,239,.88)");
        context.shadowBlur = zoom < 10 ? 0 : 3;
        context.shadowColor = path.rangeIndex % 2 === 0 ? "rgba(141,216,196,.5)" : "rgba(114,167,255,.5)";
        context.lineWidth = zoom < 9 ? 1.25 : 1.8;
        context.stroke();
      });
      context.shadowBlur = 0;
    }

    if (visibleLayers.gaps) {
      context.setLineDash([4, 5]);
      context.lineWidth = 1.2;
      context.strokeStyle = "rgba(190,199,195,.65)";
      selectedGaps.forEach((gap) => {
        const start = map.project(gap.startPosition);
        const end = map.project(gap.endPosition);
        context.beginPath();
        context.moveTo(start.x, start.y);
        context.lineTo(end.x, end.y);
        context.stroke();
      });
      context.setLineDash([]);
    }

    if (visibleLayers.stays) {
      selectedStays.forEach((stay) => {
        const point = map.project(stay.position);
        context.beginPath();
        context.arc(point.x, point.y, selection?.kind === "stay" && selection.id === stay.id ? 5 : 3, 0, Math.PI * 2);
        context.fillStyle = stay.kind === "visit" ? "rgba(217,166,87,.92)" : "rgba(157,167,163,.78)";
        context.fill();
      });
    }

    if (visibleLayers.places) {
      data.places.forEach((place) => {
        const point = map.project(place.position);
        context.beginPath();
        context.arc(point.x, point.y, 2.5 + place.frequentProbability * 3.5, 0, Math.PI * 2);
        context.fillStyle = "rgba(114,167,255,.24)";
        context.fill();
        context.strokeStyle = "rgba(168,199,255,.72)";
        context.lineWidth = 1;
        context.stroke();
      });
    }
  }, [data.places, selectedGaps, selectedPaths, selectedStays, selection, visibleLayers]);

  useEffect(() => {
    if (selectedPaths.length === 0) return;
    const frame = requestAnimationFrame(fitSelection);
    return () => {
      cancelAnimationFrame(frame);
    };
  }, [fitRequest, fitSelection, selectedPaths.length]);

  useEffect(() => {
    const frame = requestAnimationFrame(drawOverlay);
    return () => cancelAnimationFrame(frame);
  }, [drawOverlay]);

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
    <>
    <Map
      ref={mapRef}
      initialViewState={mapView}
      mapStyle={baseStyle}
      attributionControl={{ compact: true }}
      reuseMaps
      interactiveLayerIds={["stay-points", "gap-lines", "place-points"]}
      onClick={handleClick}
      onRender={drawOverlay}
      onMoveEnd={(event) => onMapViewChange({
        longitude: event.viewState.longitude,
        latitude: event.viewState.latitude,
        zoom: event.viewState.zoom,
        pitch: event.viewState.pitch,
        bearing: event.viewState.bearing,
      })}
      cursor="default"
    >
      <NavigationControl position="bottom-right" visualizePitch />

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
    <canvas ref={overlayRef} className="map-data-overlay" aria-hidden="true" />
    </>
  );
}
