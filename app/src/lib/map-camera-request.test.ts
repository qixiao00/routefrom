import test from "node:test";
import assert from "node:assert/strict";
import { queueCameraFrame } from "./map-camera-request.ts";

test("a bounds update cancelling the queued focus frame cannot consume the user's command", () => {
  const frames = new Map<number, FrameRequestCallback>(); let sequence = 0; let applied = "";
  const clock = { request: (callback: FrameRequestCallback) => { frames.set(++sequence, callback); return sequence; }, cancel: (id: number) => { frames.delete(id); } };
  const handled = { current: 0 };
  const cancel = queueCameraFrame(1, handled, () => { applied = "old bounds"; return true; }, clock);
  cancel!(); assert.equal(handled.current, 0);
  queueCameraFrame(1, handled, () => { applied = "latest bounds"; return true; }, clock);
  for (const callback of frames.values()) callback(0);
  assert.equal(applied, "latest bounds"); assert.equal(handled.current, 1);
  assert.equal(queueCameraFrame(1, handled, () => { throw new Error("duplicate focus"); }, clock), undefined);
});

test("a missing map does not acknowledge a focus command; it can run after map readiness", () => {
  let callback: FrameRequestCallback = () => {};
  const clock = { request: (next: FrameRequestCallback) => { callback = next; return 1; }, cancel: () => {} };
  const handled = { current: 0 };
  queueCameraFrame(2, handled, () => false, clock); callback(0); assert.equal(handled.current, 0);
  queueCameraFrame(2, handled, () => true, clock); callback(0); assert.equal(handled.current, 2);
});

test("native animation frame functions keep the browser Window receiver", () => {
  const oldRequest = globalThis.requestAnimationFrame, oldCancel = globalThis.cancelAnimationFrame;
  let callback: FrameRequestCallback = () => {};
  globalThis.requestAnimationFrame = function (next) { assert.equal(this, globalThis); callback = next; return 7; };
  globalThis.cancelAnimationFrame = function (id) { assert.equal(this, globalThis); assert.equal(id, 7); };
  try {
    const handled = { current: 0 }; const cancel = queueCameraFrame(3, handled, () => true);
    callback(0); assert.equal(handled.current, 3); cancel!();
  } finally { globalThis.requestAnimationFrame = oldRequest; globalThis.cancelAnimationFrame = oldCancel; }
});
