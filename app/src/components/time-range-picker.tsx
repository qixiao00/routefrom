"use client";

import { useEffect, useState } from "react";
import * as Popover from "@radix-ui/react-popover";
import { DayPicker } from "react-day-picker";
import { zhCN } from "react-day-picker/locale";
import { CalendarDays, Check, Trash2, X } from "lucide-react";

import type { TimeRange } from "@/lib/workspace-query";

type Endpoint = "start" | "end";

interface TimeRangePickerProps {
  index: number;
  range: TimeRange;
  datasetStart?: string;
  datasetEnd?: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onApply: (range: TimeRange) => void;
  onRemove: () => void;
  canRemove: boolean;
}

function shortDate(instant: string): string {
  const date = new Date(instant);
  return `${date.getFullYear()}/${String(date.getMonth() + 1).padStart(2, "0")}/${String(date.getDate()).padStart(2, "0")}`;
}

function timeOf(instant: string): string {
  const date = new Date(instant);
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

function replaceLocalDate(instant: string, day: Date): string {
  const previous = new Date(instant);
  return new Date(day.getFullYear(), day.getMonth(), day.getDate(), previous.getHours(), previous.getMinutes()).toISOString();
}

function replaceLocalTime(instant: string, time: string): string | null {
  if (!/^\d{2}:\d{2}$/.test(time)) return null;
  const [hours, minutes] = time.split(":").map(Number);
  if (hours > 23 || minutes > 59) return null;
  const previous = new Date(instant);
  return new Date(previous.getFullYear(), previous.getMonth(), previous.getDate(), hours, minutes).toISOString();
}

export function TimeRangePicker({
  index, range, datasetStart, datasetEnd, open, onOpenChange, onApply, onRemove, canRemove,
}: TimeRangePickerProps) {
  const [draft, setDraft] = useState(range);
  const [endpoint, setEndpoint] = useState<Endpoint>("start");
  const [month, setMonth] = useState(() => new Date(range.start));

  useEffect(() => {
    if (!open) return;
    setDraft(range);
    setEndpoint("start");
    setMonth(new Date(range.start));
  }, [open, range]);

  const valid = Date.parse(draft.start) < Date.parse(draft.end);
  const selected = new Date(draft[endpoint]);
  const other = new Date(draft[endpoint === "start" ? "end" : "start"]);
  const firstYear = datasetStart ? new Date(datasetStart).getFullYear() - 1 : selected.getFullYear() - 10;
  const lastYear = datasetEnd ? new Date(datasetEnd).getFullYear() + 1 : selected.getFullYear() + 10;

  return (
    <div className="range-editor">
      <span className={`slice-color ${index % 2 === 0 ? "mint" : "blue"}`} />
      <Popover.Root open={open} onOpenChange={onOpenChange}>
        <Popover.Trigger asChild>
          <button type="button" className="range-trigger" aria-label={`编辑时间区间 ${index + 1}`}>
            <CalendarDays size={15} />
            <span><small>开始</small>{shortDate(range.start)} <em>{timeOf(range.start)}</em></span>
            <span className="range-arrow">→</span>
            <span><small>结束</small>{shortDate(range.end)} <em>{timeOf(range.end)}</em></span>
          </button>
        </Popover.Trigger>
        <Popover.Portal>
          <Popover.Content className="time-picker-popover" align="start" sideOffset={8} collisionPadding={12}>
            <div className="time-picker-heading"><strong>编辑时间区间 {index + 1}</strong><Popover.Close aria-label="关闭时间选择器"><X size={15} /></Popover.Close></div>
            <div className="time-picker-endpoints">
              {(["start", "end"] as const).map((field) => (
                <button key={field} type="button" className={endpoint === field ? "active" : ""} onClick={() => { setEndpoint(field); setMonth(new Date(draft[field])); }}>
                  <small>{field === "start" ? "开始时间" : "结束时间"}</small>
                  <span>{shortDate(draft[field])} <em>{timeOf(draft[field])}</em></span>
                </button>
              ))}
            </div>
            <DayPicker
              mode="single"
              required
              locale={zhCN}
              month={month}
              onMonthChange={setMonth}
              captionLayout="dropdown"
              startMonth={new Date(firstYear, 0)}
              endMonth={new Date(lastYear, 11)}
              selected={selected}
              modifiers={{ other }}
              onSelect={(day) => {
                if (day) setDraft((current) => ({ ...current, [endpoint]: replaceLocalDate(current[endpoint], day) }));
              }}
              className="range-calendar"
            />
            <div className="time-picker-clock">
              <label htmlFor={`range-time-${index}`}>{endpoint === "start" ? "开始时刻" : "结束时刻"}</label>
              <input id={`range-time-${index}`} type="time" step={60} value={timeOf(draft[endpoint])} onChange={(event) => {
                const instant = replaceLocalTime(draft[endpoint], event.target.value);
                if (instant) setDraft((current) => ({ ...current, [endpoint]: instant }));
              }} />
              <span>本机时区</span>
            </div>
            {!valid && <p className="time-picker-error" role="alert">结束时间需要晚于开始时间。</p>}
            <div className="time-picker-actions">
              <Popover.Close type="button">取消</Popover.Close>
              <button type="button" className="primary" disabled={!valid} onClick={() => { onApply(draft); onOpenChange(false); }}><Check size={14} />应用区间</button>
            </div>
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>
      <button type="button" className="range-remove" onClick={onRemove} disabled={!canRemove} aria-label={`删除时间区间 ${index + 1}`}><Trash2 size={13} /></button>
    </div>
  );
}
