import type { PreviewPlace } from "./workspace-data.ts";

/** Preview place indices can shift after reprocessing; include provenance to avoid mislabeling a different place. */
export function placeNoteKey(datasetId: string, place: PreviewPlace): string {
  return `${datasetId}:${place.id}:${place.firstVisitedAt}:${place.position[0].toFixed(4)}:${place.position[1].toFixed(4)}`;
}

export function placeDisplayName(datasetId: string, place: PreviewPlace, notes: Record<string, string>): string {
  // Presence of the cloud field (even null) is authoritative. A deleted cloud
  // label must not reappear from a browser-only legacy record.
  if (place.note !== undefined) return place.note?.label || place.name;
  return notes[placeNoteKey(datasetId, place)] || place.name;
}
