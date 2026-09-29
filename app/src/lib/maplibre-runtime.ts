import * as maplibre from "maplibre-gl";

// Configure the worker before react-map-gl constructs its first Map. These
// same-version assets are prepared by both the dev and production build scripts.
maplibre.setWorkerUrl(`/maplibre/${maplibre.getVersion()}/maplibre-gl-worker.mjs`);

export { maplibre };
