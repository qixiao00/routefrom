export interface PlaceNoteRecord {
  id: string;
  label: string;
  revision: number;
  position: [number, number];
  placeIds: string[];
  status: "bound" | "unmatched" | "ambiguous" | "collision";
}

export interface PlaceNoteCollection {
  runId: string | null;
  lineageReady: boolean;
  notes: PlaceNoteRecord[];
}

/** Exact evidence only. Multiple labels for one merged place are not overwritten. */
export function classifyPlaceNotes(notes: Omit<PlaceNoteRecord, "status">[]): PlaceNoteRecord[] {
  const counts = new Map<string, number>();
  for (const note of notes) for (const place of new Set(note.placeIds)) counts.set(place, (counts.get(place) ?? 0) + 1);
  return notes.map(note => ({ ...note, status: note.placeIds.length === 0 ? "unmatched"
    : note.placeIds.length > 1 ? "ambiguous" : counts.get(note.placeIds[0])! > 1 ? "collision" : "bound" }));
}
