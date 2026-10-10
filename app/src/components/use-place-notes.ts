"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { PlaceNoteCollection, PlaceNoteRecord } from "@/lib/place-note-records";
import { loadPlaceNotes, writePlaceNote } from "@/lib/place-notes-client";
import type { PreviewPlace } from "@/lib/workspace-data";

export function usePlaceNotes(subject: string, runId: string | null, ready: boolean) {
  const collectionKey = `${subject}:${runId ?? "empty"}:${ready}`;
  const [storedCollection, setStoredCollection] = useState<{ key: string; value: PlaceNoteCollection } | null>(null);
  const collection = storedCollection?.key === collectionKey ? storedCollection.value : null;
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const scope = useRef<AbortController | null>(null);
  const writing = useRef(false);
  const readSequence = useRef(0);
  const reload = useCallback(async () => {
    const controller = scope.current;
    if (!ready || !controller || controller.signal.aborted) throw new Error("地点备注尚未准备好，请稍后再试。");
    const sequence = ++readSequence.current;
    try {
      const result = await loadPlaceNotes(subject, runId, controller.signal);
      if (controller.signal.aborted || sequence !== readSequence.current) return;
      setStoredCollection({ key: collectionKey, value: result }); setError(null);
    } catch (cause) {
      if (!controller.signal.aborted && sequence === readSequence.current) setError(cause instanceof Error ? cause.message : "读取备注失败。");
      throw cause;
    }
  }, [subject, runId, ready, collectionKey]);
  useEffect(() => {
    const controller = new AbortController(); scope.current = controller;
    setStoredCollection(null); setError(null); setBusy(false);
    if (ready) void reload().catch(() => {});
    return () => controller.abort();
  }, [reload, ready]);
  const change = async (note: PlaceNoteRecord | null, label: string, placeId: string | null) => {
    const controller = scope.current;
    if (!collection || !controller || controller.signal.aborted) throw new Error("请先成功读取账号备注再保存。");
    if (writing.current) throw new Error("另一条备注正在保存，请稍后再试。");
    writing.current = true; setBusy(true); readSequence.current++;
    try {
      await writePlaceNote(subject, { id: note?.id ?? null, revision: note?.revision ?? 0, label, placeId, runId }, controller.signal);
      if (controller.signal.aborted) throw new Error("地图已切换，请重新读取账号备注。");
      // Acknowledged mutation followed by an authoritative read: never claim a
      // stale local label is the cloud version after a timeout or concurrent edit.
      await reload();
    } catch (cause) {
      if (!controller.signal.aborted) {
        setError(cause instanceof Error ? cause.message : "保存备注失败。");
        // Unknown response outcome must be reconciled before the next write.
        setStoredCollection(null);
      }
      throw cause;
    } finally { writing.current = false; if (!controller.signal.aborted) setBusy(false); }
  };
  const saveForPlace = async (place: PreviewPlace, label: string) => {
    if (!collection) throw new Error("请先在地点备注面板重新读取账号备注。");
    const candidates = collection.notes.filter(note => note.placeIds.includes(place.id));
    if ((place.note?.id ?? null) !== (candidates[0]?.id ?? null)
      || (place.note && place.note.revision !== candidates[0]?.revision)) {
      throw new Error("账号备注已更新，请重新打开蓝点备注后确认内容。当前草稿未保存。");
    }
    if (candidates.length > 1 || candidates[0]?.status === "ambiguous") throw new Error("这个地点有待确认的备注，请先在地点备注列表中整理。");
    if (!label.trim() && !candidates.length) return;
    await change(candidates[0] ?? null, label, place.id);
  };
  return { collection, error, busy, reload, change, saveForPlace };
}
