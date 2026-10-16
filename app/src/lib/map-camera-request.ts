/** Mark a camera command handled only when its frame actually applies it. */
export function queueCameraFrame(request: number, handled: { current: number }, apply: () => boolean,
  frames = { request: (callback: FrameRequestCallback) => globalThis.requestAnimationFrame(callback),
    cancel: (id: number) => globalThis.cancelAnimationFrame(id) }): (() => void) | undefined {
  if (!request || handled.current === request) return;
  const frame = frames.request(() => { if (apply()) handled.current = request; });
  return () => frames.cancel(frame);
}
