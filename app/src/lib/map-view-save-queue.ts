import type { MapViewConfig } from "./map-view-config.ts";

export type ViewSyncStatus = "loading" | "saved" | "pending" | "saving" | "error" | "conflict";
export interface ViewSyncState { status: ViewSyncStatus; message: string | null }

// One write at a time. Edits made during a request replace the pending snapshot,
// not the in-flight snapshot; failed/ambiguous writes must be reread before retry.
export class MapViewSaveQueue {
  private revision: number;
  private acknowledged: string | null;
  private latest: MapViewConfig | null = null;
  private busy = false;
  private stopped = false;
  private disposed = false;
  private write: (config: MapViewConfig, revision: number) => Promise<{ revision: number }>;
  private publish: (state: ViewSyncState) => void;

  constructor(revision: number, config: MapViewConfig | null,
    write: (config: MapViewConfig, revision: number) => Promise<{ revision: number }>,
    publish: (state: ViewSyncState) => void) {
    this.revision = revision;
    this.acknowledged = config ? JSON.stringify(config) : null;
    this.write = write;
    this.publish = publish;
  }

  update(config: MapViewConfig) {
    this.latest = config;
    if (!this.stopped && !this.disposed && !this.busy) this.publish({ status: this.dirty ? "pending" : "saved", message: null });
  }

  get dirty() { return !!this.latest && JSON.stringify(this.latest) !== this.acknowledged; }

  async flush() {
    if (this.busy || this.stopped || this.disposed || !this.dirty) return;
    this.busy = true;
    this.publish({ status: "saving", message: null });
    try {
      while (!this.disposed && !this.stopped && this.dirty && this.latest) {
        const config = this.latest;
        const result = await this.write(config, this.revision);
        if (this.disposed) return;
        this.revision = result.revision;
        this.acknowledged = JSON.stringify(config);
      }
      if (!this.disposed) this.publish({ status: "saved", message: null });
    } catch (error) {
      if (this.disposed) return;
      this.stopped = true;
      this.publish({ status: error && typeof error === "object" && "code" in error && error.code === "view_conflict" ? "conflict" : "error",
        message: error instanceof Error ? error.message : "保存失败，本地改动仍保留。" });
    } finally { this.busy = false; }
  }

  dispose() { this.disposed = true; }
}
