"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FeatureCollection, LineString, MultiLineString, Point } from "geojson";
import type { MapLayerMouseEvent, MapRef } from "react-map-gl/maplibre";
import Map, { Layer, Marker, NavigationControl, Popup, Source } from "react-map-gl/maplibre";
import { maplibre } from "@/lib/maplibre-runtime";
import { buildTrackGeoJSON } from "@/lib/map-track-data";
import { buildPlaceGeoJSON } from "@/lib/map-place-data";
import { placeDisplayName, placeNoteKey } from "@/lib/place-notes";

import type {
  PreviewInferredConnection,
  PreviewPlace,
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
  placeNotes: Record<string, string>;
  selection: { kind: SelectionKind; id: string } | null;
  mapView: MapView;
  fitRequest: number;
  globeRequest: number;
  onMapViewChange: (view: MapView) => void;
  onViewportChange: (bounds: ViewportBounds) => void;
  onSelect: (selection: { kind: SelectionKind; id: string } | null) => void;
  onSavePlaceNote: (place: PreviewPlace, note: string) => void;
}

// Vector tiles keep the globe crisp at a distance and the street map legible up close.
const baseStyle = "https://tiles.openfreemap.org/styles/dark";

export function MapCanvas({
  data,
  selectedPaths,
  aggregatedRoutes,
  selectedBounds,
  selectedStays,
  selectedInferredConnections,
  visibleLayers,
  placeNotes,
  selection,
  mapView,
  fitRequest,
  globeRequest,
  onMapViewChange,
  onViewportChange,
  onSelect,
  onSavePlaceNote,
}: MapCanvasProps) {
  const mapRef = useRef<MapRef>(null);
  const handledFitRequest = useRef(0);
  const handledGlobeRequest = useRef(0);
  const lastReportedBounds = useRef<ViewportBounds | null>(null);
  const lastDiagnostic = useRef("");
  const [placeGroup, setPlaceGroup] = useState<{ position: number[]; ids: string[] } | null>(null);
  const [editingPlace, setEditingPlace] = useState<PreviewPlace | null>(null);
  const [draftNote, setDraftNote] = useState("");
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
      features: (visibleLayers.stays ? selectedStays : []).map((stay) => ({
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
    [selectedStays, selection, visibleLayers.stays],
  );
  const placeData = useMemo<FeatureCollection<Point>>(
    () => visibleLayers.places
      ? buildPlaceGeoJSON(data.places.map((place) => ({ ...place, name: placeDisplayName(data.dataset.id, place, placeNotes) })), mapView.zoom, selection?.kind === "place" ? selection.id : undefined)
      : { type: "FeatureCollection", features: [] },
    [data.dataset.id, data.places, placeNotes, selection, mapView.zoom, visibleLayers.places],
  );

  const beginPlaceNote = useCallback((place: PreviewPlace) => {
    setPlaceGroup(null);
    setEditingPlace(place);
    setDraftNote(placeNotes[placeNoteKey(data.dataset.id, place)] ?? "");
  }, [data.dataset.id, placeNotes]);

  // Hide immediately; the GeoJSON source can take another frame to rebuild large selections.
  useEffect(() => {
    const map = mapRef.current?.getMap();
    if (!map) return;
    const layerVisibility = [
      ["track-lines", visibleLayers.track],
      ["sparse-lines", visibleLayers.sparse],
      ["high-speed-lines", visibleLayers.highSpeed],
      ["stay-points", visibleLayers.stays],
      ["inferred-lines", visibleLayers.gaps],
      ["place-points", visibleLayers.places],
      ["place-labels", visibleLayers.places],
    ] as const;
    for (const [id, visible] of layerVisibility) {
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", visible ? "visible" : "none");
    }
  }, [visibleLayers]);

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
    if (globeRequest === 0 || globeRequest === handledGlobeRequest.current) return;
    handledGlobeRequest.current = globeRequest;
    mapRef.current?.flyTo({
      center: [mapView.longitude, Math.max(-55, Math.min(55, mapView.latitude))],
      zoom: 1.7,
      pitch: 0,
      bearing: 0,
      duration: 1100,
    });
  }, [globeRequest, mapView.latitude, mapView.longitude]);

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
      onContextMenu={(event) => {
        event.originalEvent.preventDefault();
        const feature = event.features?.find((item) => item.properties?.kind === "place");
        if (!feature || feature.geometry.type !== "Point") return;
        const ids = JSON.parse(feature.properties!.memberIds) as string[];
        if (ids.length > 1) {
          setEditingPlace(null);
          setPlaceGroup({ position: feature.geometry.coordinates, ids });
        } else {
          const place = data.places.find((item) => item.id === ids[0]);
          if (place) beginPlaceNote(place);
        }
      }}
      onLoad={() => {
        const map = mapRef.current?.getMap();
        map?.setProjection({ type: "globe" });
        map?.setSky({
          "sky-color": "#090f1a",
          "horizon-color": "#253d4b",
          "fog-color": "#12242b",
          "fog-ground-blend": 0.32,
          "horizon-fog-blend": 0.45,
          "sky-horizon-blend": 0.68,
          "atmosphere-blend": 0.75,
        });
        reportViewport();
      }}
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
          <button className="place-group-badge" aria-label={feature.properties!.name} title={feature.properties!.name} onContextMenu={event => {
            event.preventDefault();
            event.stopPropagation();
            setPlaceGroup({ position: feature.geometry.coordinates, ids: JSON.parse(feature.properties!.memberIds) });
          }} onClick={event => {
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
              <div className="place-group-row" key={p.id}>
                <button type="button" onClick={() => { onSelect({ kind: "place", id: p.id }); setPlaceGroup(null); }}>{placeDisplayName(data.dataset.id, p, placeNotes)} · {p.visitCount} 次</button>
                <button type="button" onClick={() => beginPlaceNote(p)}>备注</button>
              </div>
            ))}
          </div>
        </Popup>
      )}
      {editingPlace && visibleLayers.places && (
        <Popup longitude={editingPlace.position[0]} latitude={editingPlace.position[1]} anchor="right" onClose={() => setEditingPlace(null)} closeOnClick={false} maxWidth="290px" className="place-note-popup">
          <form onSubmit={(event) => {
            event.preventDefault();
            onSavePlaceNote(editingPlace, draftNote);
            setEditingPlace(null);
          }}>
            <span className="place-note-eyebrow">常去地点 · 我的备注</span>
            <strong>{placeDisplayName(data.dataset.id, editingPlace, placeNotes)}</strong>
            <label htmlFor="place-note-input">这是哪里？</label>
            <input id="place-note-input" autoFocus maxLength={80} value={draftNote} onChange={(event) => setDraftNote(event.target.value)} placeholder="例如：家、公司、常去的咖啡店" />
            <span className="place-note-hint">只保存在此浏览器。清空后保存可移除备注。</span>
            <div className="place-note-actions"><button type="button" onClick={() => setEditingPlace(null)}>取消</button><button type="submit">保存备注</button></div>
          </form>
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
