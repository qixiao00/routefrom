"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FeatureCollection, LineString, MultiLineString, Point } from "geojson";
import type { MapLayerMouseEvent, MapRef } from "react-map-gl/maplibre";
import Map, { Layer, Marker, NavigationControl, Popup, Source } from "react-map-gl/maplibre";
import { maplibre } from "@/lib/maplibre-runtime";
import { buildTrackGeoJSON } from "@/lib/map-track-data";
import { buildPlaceGeoJSON } from "@/lib/map-place-data";

import type {
  PreviewInferredConnection,
  PreviewStay,
  WorkspacePreview,
} from "@/lib/workspace-data";
import type { LayerId, MapView, SelectionKind } from "@/lib/workspace-store";
import type { ViewportBounds, ViewportPath } from "@/lib/workspace-viewport";

interface MapCanvasProps {
  data: WorkspacePreview;
  selectedPaths: ViewportPath[];
  aggregatedRoutes?: FeatureCollection<MultiLineString>;
  selectedBounds: ViewportBounds | null;
  selectedStays: PreviewStay[];
  selectedInferredConnections: PreviewInferredConnection[];
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
  aggregatedRoutes,
  selectedBounds,
  selectedStays,
  selectedInferredConnections,
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
  const lastReportedBounds = useRef<ViewportBounds | null>(null);
  const lastDiagnostic = useRef("");
  const [placeGroup, setPlaceGroup] = useState<{ position: number[]; ids: string[] } | null>(null);
  const denseHistory = selectedPaths.length > 500;
  const lineData = useMemo(
    () => ({ type: "FeatureCollection" as const, features: [
      ...(visibleLayers.track && aggregatedRoutes ? aggregatedRoutes.features : []),
      ...buildTrackGeoJSON(selectedPaths, { ...visibleLayers, track: visibleLayers.track && !aggregatedRoutes }).features,
    ] }),
    [selectedPaths, aggregatedRoutes, visibleLayers.track, visibleLayers.sparse, visibleLayers.highSpeed],
  );
  const inferredData = useMemo<FeatureCollection<LineString>>(
    () => ({
      type: "FeatureCollection",
      features: (visibleLayers.gaps ? selectedInferredConnections : []).map((connection) => ({
        type: "Feature",
        properties: { id: connection.gapId, kind: "gap", confidence: connection.confidence },
        geometry: {
          type: "LineString",
          coordinates: [connection.startPosition, connection.endPosition],
        },
      })),
    }),
    [selectedInferredConnections, visibleLayers.gaps],
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
    () => buildPlaceGeoJSON(data.places, mapView.zoom, selection?.kind === "place" ? selection.id : undefined),
    [data.places, selection, mapView.zoom],
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
    const nextBounds: ViewportBounds = west < east && south < north
      ? [west, south, east, north]
      : [-180, -90, 180, 90];
    if (lastReportedBounds.current?.every((value, index) =>
      Math.abs(value - nextBounds[index]) < 1e-6
    )) return;
    lastReportedBounds.current = nextBounds;
    onViewportChange(nextBounds);
  }, [onViewportChange]);

  useEffect(() => {
    let attempts = 0;
    let timer: ReturnType<typeof setTimeout>;
    const reportWhenReady = () => {
      if (mapRef.current?.getMap()) {
        reportViewport();
      } else if (attempts++ < 100) {
        timer = setTimeout(reportWhenReady, 100);
      }
    };
    reportWhenReady();
    return () => clearTimeout(timer);
  }, [reportViewport]);

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
    if (feature && feature.properties?.placeCount > 1 && feature.geometry.type === "Point") {
      setPlaceGroup({ position: feature.geometry.coordinates, ids: JSON.parse(feature.properties.memberIds) });
      return;
    }
    setPlaceGroup(null);
    const kind = feature?.properties?.kind as SelectionKind | undefined;
    const id = feature?.properties?.id as string | undefined;
    onSelect(kind && id ? { kind, id } : null);
  }

  return (
    <Map
      ref={mapRef}
      mapLib={maplibre}
      initialViewState={mapView}
      mapStyle={baseStyle}
      attributionControl={{ compact: true }}
      interactiveLayerIds={[
        ...(visibleLayers.places ? ["place-points"] : []),
        ...(visibleLayers.stays ? ["stay-points"] : []),
        ...(visibleLayers.gaps ? ["inferred-lines"] : []),
      ]}
      onClick={handleClick}
      onLoad={reportViewport}
      onIdle={() => {
        if (process.env.NODE_ENV !== "development") return;
        const map = mapRef.current?.getMap();
        if (map?.getLayer("track-lines") && selectedPaths.length) {
          const diagnostic = {
            workerUrl: maplibre.getWorkerUrl(),
            paths: selectedPaths.length,
            sourceLoaded: map.isSourceLoaded("observed-tracks"),
            sourceFeatures: map.querySourceFeatures("observed-tracks").length,
            renderedTracks: map.queryRenderedFeatures({ layers: ["track-lines"] }).length,
            renderedStays: map.queryRenderedFeatures({ layers: ["stay-points"] }).length,
          };
          const signature = JSON.stringify(diagnostic);
          if (signature !== lastDiagnostic.current) {
            lastDiagnostic.current = signature;
            console.debug("[routefrom-map]", diagnostic);
          }
        }
      }}
      onRender={() => {
        if (!lastReportedBounds.current) reportViewport();
      }}
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
      {visibleLayers.places && placeData.features.filter(f => f.properties!.placeCount > 1).map(feature => (
        <Marker key={feature.properties!.memberIds} longitude={feature.geometry.coordinates[0]} latitude={feature.geometry.coordinates[1]} anchor="center">
          <button className="place-group-badge" aria-label={feature.properties!.name} title={feature.properties!.name} onClick={event => {
            event.stopPropagation();
            setPlaceGroup({ position: feature.geometry.coordinates, ids: JSON.parse(feature.properties!.memberIds) });
          }}>{feature.properties!.placeCount}</button>
        </Marker>
      ))}
      {placeGroup && visibleLayers.places && (
        <Popup longitude={placeGroup.position[0]} latitude={placeGroup.position[1]} onClose={() => setPlaceGroup(null)} closeOnClick={false} maxWidth="260px" className="place-group-popup">
          <strong>{placeGroup.ids.length} 个重叠地点</strong>
          <p>共 {data.places.filter(p => placeGroup.ids.includes(p.id)).reduce((n, p) => n + p.visitCount, 0)} 次访问记录</p>
          <button onClick={() => {
            mapRef.current?.flyTo({ center: [placeGroup.position[0], placeGroup.position[1]], zoom: Math.min(20, mapView.zoom + 2), duration: 500 });
            setPlaceGroup(null);
          }}>放大查看</button>
          <div style={{ maxHeight: 180, overflowY: "auto" }}>
            {data.places.filter(p => placeGroup.ids.includes(p.id)).map(p => (
              <button key={p.id} style={{ display: "block", marginTop: 8 }} onClick={() => { onSelect({ kind: "place", id: p.id }); setPlaceGroup(null); }}>{p.name} · {p.visitCount} 次</button>
            ))}
          </div>
        </Popup>
      )}

      <Source key="observed-tracks" id="observed-tracks" type="geojson" data={lineData}>
        <Layer id="track-lines" type="line" filter={["==", ["get", "movementClass"], "ordinary"]} layout={{ "line-cap": "round", "line-join": "round" }} paint={{ "line-color": ["case", ["==", ["get", "paletteIndex"], 0], "#9be2cf", "#7dafef"], "line-width": ["interpolate", ["linear"], ["zoom"], 4, ["interpolate", ["linear"], ["get", "traversalCount"], 1, 1, 3, 2, 10, 3.5, 30, 5], 15, ["interpolate", ["linear"], ["get", "traversalCount"], 1, denseHistory ? 1.4 : 2.2, 3, 3.5, 10, 5.5, 30, 8]], "line-opacity": visibleLayers.track ? ["interpolate", ["linear"], ["get", "traversalCount"], 1, denseHistory ? 0.16 : 0.42, 2, denseHistory ? 0.34 : 0.57, 3, denseHistory ? 0.48 : 0.66, 5, 0.65, 10, 0.8, 20, 0.87] : 0 }} />
        <Layer id="sparse-lines" type="line" filter={["==", ["get", "movementClass"], "sparse"]} layout={{ "line-cap": "butt", "line-join": "round" }} paint={{ "line-color": "#9aa8a5", "line-width": ["interpolate", ["linear"], ["zoom"], 4, 0.8, 10, 1.1, 15, 1.5], "line-opacity": visibleLayers.sparse ? 0.45 : 0, "line-dasharray": [2, 3] }} />
        <Layer id="high-speed-lines" type="line" filter={["==", ["get", "movementClass"], "high_speed"]} layout={{ "line-cap": "butt", "line-join": "round" }} paint={{ "line-color": "#8aa9bb", "line-width": ["interpolate", ["linear"], ["zoom"], 4, 1, 10, 1.3, 15, 1.9], "line-opacity": visibleLayers.highSpeed ? 0.58 : 0, "line-dasharray": [3, 2.5] }} />
      </Source>

      <Source key="inferred-connections" id="inferred-connections" type="geojson" data={inferredData}>
        <Layer id="inferred-lines" type="line" layout={{ "line-cap": "butt" }} paint={{ "line-color": "#a3aea9", "line-width": 1.2, "line-opacity": 0.5, "line-dasharray": [2, 3] }} />
      </Source>

      <Source key="stationary-events" id="stationary-events" type="geojson" data={stayData}>
        <Layer id="stay-points" type="circle" paint={{ "circle-radius": ["case", ["get", "selected"], 8, ["==", ["get", "eventKind"], "visit"], 5, 3.5], "circle-color": ["case", ["==", ["get", "eventKind"], "visit"], "#d9a657", ["==", ["get", "eventKind"], "transport_pause"], "#9da7a3", "#6f7a76"], "circle-opacity": visibleLayers.stays ? 0.9 : 0, "circle-stroke-width": ["case", ["get", "selected"], 3, 1.2], "circle-stroke-color": "#f5ead7" }} />
      </Source>

      <Source key="frequent-places" id="frequent-places" type="geojson" data={placeData}>
        <Layer id="place-points" type="circle" paint={{ "circle-radius": ["case", [">", ["get", "placeCount"], 1], 12, ["interpolate", ["linear"], ["get", "frequency"], 0, 5, 1, 11]], "circle-color": "#72a7ff", "circle-opacity": visibleLayers.places ? 0.3 : 0, "circle-stroke-width": ["case", ["get", "selected"], 2.5, 1], "circle-stroke-color": "#a8c7ff" }} />
        <Layer id="place-labels" type="symbol" minzoom={10} layout={{ "text-field": ["get", "name"], "text-size": 11, "text-offset": [0, 1.4], "text-anchor": "top", "text-allow-overlap": false }} paint={{ "text-color": "#c8d7e9", "text-halo-color": "#0a0e10", "text-halo-width": 1.5, "text-opacity": visibleLayers.places ? 0.78 : 0 }} />
      </Source>
    </Map>
  );
}
