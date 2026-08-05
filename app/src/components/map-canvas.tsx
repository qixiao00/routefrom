"use client";

import type { FeatureCollection, LineString, Point } from "geojson";
import type { StyleSpecification } from "maplibre-gl";
import Map, { Layer, Marker, NavigationControl, Source } from "react-map-gl/maplibre";

const baseStyle: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [
    {
      id: "background",
      type: "background",
      paint: { "background-color": "#0b1012" },
    },
  ],
};

const contextLines: FeatureCollection<LineString> = {
  type: "FeatureCollection",
  features: [
    [[121.35, 31.19], [121.57, 31.19]],
    [[121.34, 31.215], [121.58, 31.215]],
    [[121.36, 31.245], [121.56, 31.245]],
    [[121.39, 31.17], [121.39, 31.28]],
    [[121.435, 31.17], [121.435, 31.28]],
    [[121.48, 31.17], [121.48, 31.28]],
    [[121.525, 31.17], [121.525, 31.28]],
    [[121.36, 31.18], [121.55, 31.265]],
    [[121.38, 31.275], [121.57, 31.185]],
  ].map((coordinates) => ({
    type: "Feature",
    properties: {},
    geometry: { type: "LineString", coordinates },
  })),
};

const route: FeatureCollection<LineString> = {
  type: "FeatureCollection",
  features: [
    {
      type: "Feature",
      properties: {},
      geometry: {
        type: "LineString",
        coordinates: [
          [121.395, 31.235],
          [121.421, 31.224],
          [121.448, 31.229],
          [121.473, 31.238],
          [121.491, 31.218],
          [121.509, 31.204],
          [121.531, 31.216],
        ],
      },
    },
  ],
};

const routePoints: FeatureCollection<Point> = {
  type: "FeatureCollection",
  features: route.features[0].geometry.coordinates.map((coordinates, index) => ({
    type: "Feature",
    properties: { index },
    geometry: { type: "Point", coordinates },
  })),
};

const places = [
  { name: "静安", longitude: 121.448, latitude: 31.229 },
  { name: "陆家嘴", longitude: 121.509, latitude: 31.204 },
];

export function MapCanvas() {
  return (
    <Map
      initialViewState={{ longitude: 121.47, latitude: 31.225, zoom: 11.45, pitch: 36, bearing: -8 }}
      mapStyle={baseStyle}
      attributionControl={false}
      reuseMaps
    >
      <NavigationControl position="bottom-right" showCompass={false} />

      <Source id="context-lines" type="geojson" data={contextLines}>
        <Layer
          id="context-line-layer"
          type="line"
          paint={{
            "line-color": "#6c7774",
            "line-width": 1,
            "line-opacity": 0.13,
          }}
        />
      </Source>

      <Source id="route" type="geojson" data={route}>
        <Layer
          id="route-glow"
          type="line"
          paint={{
            "line-color": "#66e3c4",
            "line-width": 12,
            "line-opacity": 0.12,
            "line-blur": 8,
          }}
        />
        <Layer
          id="route-line"
          type="line"
          paint={{
            "line-color": "#72efcf",
            "line-width": 3,
            "line-opacity": 0.92,
          }}
          layout={{ "line-cap": "round", "line-join": "round" }}
        />
      </Source>

      <Source id="route-points" type="geojson" data={routePoints}>
        <Layer
          id="route-point-layer"
          type="circle"
          paint={{
            "circle-radius": 3.5,
            "circle-color": "#d7fff4",
            "circle-stroke-width": 1.5,
            "circle-stroke-color": "#173f38",
          }}
        />
      </Source>

      {places.map((place) => (
        <Marker key={place.name} longitude={place.longitude} latitude={place.latitude} anchor="bottom">
          <div className="place-marker">
            <span />
            {place.name}
          </div>
        </Marker>
      ))}
    </Map>
  );
}
