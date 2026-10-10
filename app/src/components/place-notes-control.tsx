"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import * as Popover from "@radix-ui/react-popover";
import { MapPin, RefreshCw } from "lucide-react";
import type { PreviewPlace } from "@/lib/workspace-data";
import type { PlaceNoteRecord } from "@/lib/place-note-records";
import { requestPlaceNotes } from "@/lib/place-notes-client";
import type { usePlaceNotes } from "./use-place-notes";

export function PlaceNotesControl({ subject, places, notes, legacyNotes, ready }: {
  subject: string; places: PreviewPlace[]; notes: ReturnType<typeof usePlaceNotes>;
  legacyNotes: Record<string, string>; ready: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<PlaceNoteRecord | null>(null);
  const [label, setLabel] = useState("");
  const [target, setTarget] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const operation = useRef(false);
  const act = async (fn: () => Promise<void>) => {
    if (operation.current) return;
    operation.current = true; setActionBusy(true); setError(null);
    try { await fn(); } catch (cause) { setError(cause instanceof Error ? cause.message : "操作失败。"); }
    finally { operation.current = false; setActionBusy(false); }
  };
  const busy = actionBusy || notes.busy;
  const statuses = { bound: "已关联地点", unmatched: "暂无可关联地点", ambiguous: "来源分属多个地点", collision: "地点合并，备注待确认" };
  return <Popover.Root open={open} onOpenChange={value => { setOpen(value); }}>
    <Popover.Trigger asChild><button type="button" className={`map-view-sync ${notes.error ? "has-error" : ""}`} aria-label="地点备注与同步" disabled={!ready}><MapPin size={14} /><span>地点备注</span></button></Popover.Trigger>
    <Popover.Portal><Popover.Content className="map-views-popover place-notes-panel" align="end" sideOffset={12} collisionPadding={12}>
      <div className="map-views-heading"><div><small>你认识的地方，随账号保留</small><h2>地点备注</h2></div><MapPin size={20} /></div>
      <p className="map-views-hint">右击蓝色地点添加名称。重算后按原始观测关联；找不到来源或多个名称重叠时，保留备注等你确认。</p>
      <div className={`map-views-status ${notes.error ? "has-error" : ""}`} role="status"><strong>{notes.collection ? `${notes.collection.notes.length} 条账号备注` : "备注尚未读取"}</strong>
        <span>{notes.error ?? "备注保存不改变轨迹或算法结果。"}</span>
        <button type="button" disabled={busy} onClick={() => void act(notes.reload)}><RefreshCw size={13} />重新读取账号备注</button>
      </div>
      {notes.collection && !notes.collection.lineageReady && <div className="map-views-status has-error">
        <span>当前是旧处理版本，需要补充地点来源后才能保存新备注。现有地图和浏览器备注会保留。</span>
        <button type="button" disabled={busy} onClick={() => void act(async () => {
          await requestPlaceNotes(subject, "/api/library/reprocess", "POST"); window.location.assign("/library");
        })}>重新处理足迹</button>
      </div>}
      {(error || notes.error) && <p role="alert" className="map-views-operation-error">{error || notes.error}</p>}
      <div className="saved-map-view-list">
        {notes.collection?.notes.map(note => <div className="saved-map-view" key={note.id}><button type="button" disabled={busy || !notes.collection} onClick={() => { setEditing(note); setLabel(note.label); setTarget(""); }}>
          <strong>{note.label}</strong><small>{statuses[note.status]}</small>
        </button></div>)}
      </div>
      {editing && <form className="map-view-save-form" onSubmit={event => { event.preventDefault(); void act(async () => {
        const current = notes.collection?.notes.find(item => item.id === editing.id);
        if (!current) throw new Error("请重新读取这条备注后再保存，草稿仍保留。");
        // Keep the revision opened by the editor; a later read must not grant
        // permission to overwrite another device's update without confirmation.
        await notes.change(editing, label, target || null); setEditing(null);
      }); }}>
        <label htmlFor="account-place-note">修改「{editing.label}」</label>
        <input id="account-place-note" value={label} maxLength={80} disabled={busy} onChange={event => setLabel(event.target.value)} />
        <label htmlFor="place-note-rebind">地点关联</label>
        <select id="place-note-rebind" value={target} disabled={busy || !notes.collection?.lineageReady} onChange={event => setTarget(event.target.value)}>
          <option value="">保留原始来源关联</option>{places.map(place => <option key={place.id} value={place.id}>{place.note?.label || place.name} · {place.position.map(value => value.toFixed(4)).join(", ")}</option>)}
        </select>
        <div><button type="button" disabled={busy} onClick={() => setEditing(null)}>取消</button><button type="submit" disabled={busy || !notes.collection || !label.trim()}>保存账号备注</button></div>
        <button type="button" disabled={busy || !notes.collection} onClick={() => {
          if (!window.confirm(`删除备注「${editing.label}」？不会删除地点或足迹。`)) return;
          void act(async () => { await notes.change(editing, "", null); setEditing(null); });
        }}>删除这条备注</button>
      </form>}
      {!!Object.keys(legacyNotes).length && <details className="legacy-place-notes"><summary>浏览器旧备注 · {Object.keys(legacyNotes).length} 条</summary>
        <p>这些记录尚未保存到账号。请确认对应蓝点后重新备注；不会按附近位置自动迁移。</p>
        {Object.entries(legacyNotes).map(([key, value]) => <div key={key}><strong>{value}</strong><code title={key}>{key}</code></div>)}
      </details>}
      <Link className="place-notes-library-link" href="/library">查看导入与处理进度</Link>
      <Popover.Arrow className="map-views-arrow" />
    </Popover.Content></Popover.Portal>
  </Popover.Root>;
}
