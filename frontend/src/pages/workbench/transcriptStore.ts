import type { TranscriptMessage } from "./types";

type Listener = () => void;

export class TranscriptStore {
  private order: TranscriptMessage[] = [];
  private byId = new Map<string, TranscriptMessage>();
  private listeners = new Set<Listener>();
  private rowListeners = new Map<string, Set<Listener>>();
  private dirty = new Set<string>();
  private frame: number | null = null;

  snapshot = () => this.order;
  get = (id: string) => this.byId.get(id);
  read = () => this.order.map((row) => this.byId.get(row.id) ?? row);

  subscribe = (listener: Listener) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };

  subscribeRow = (id: string, listener: Listener) => {
    const listeners = this.rowListeners.get(id) ?? new Set();
    listeners.add(listener);
    this.rowListeners.set(id, listeners);
    return () => {
      listeners.delete(listener);
      if (!listeners.size) this.rowListeners.delete(id);
    };
  };

  set = (value: TranscriptMessage[] | ((current: TranscriptMessage[]) => TranscriptMessage[])) => {
    const next = typeof value === "function" ? value(this.read()) : value;
    const byId = new Map(next.map((row) => [row.id, row]));
    for (const [id, previous] of this.byId) {
      if (byId.get(id) !== previous) this.dirty.add(id);
    }
    for (const id of this.rowListeners.keys()) {
      if (byId.has(id) && !this.byId.has(id)) this.dirty.add(id);
    }
    this.byId = byId;
    if (this.order.length !== next.length || this.order.some((row, index) => row.id !== next[index].id)) {
      this.order = next;
      this.listeners.forEach((listener) => listener());
    }
    this.flush();
  };

  patch(id: string, update: (current: TranscriptMessage) => TranscriptMessage, defer = false) {
    const current = this.byId.get(id);
    if (!current) return false;
    const next = update(current);
    if (next !== current) {
      this.byId.set(id, next);
      this.dirty.add(id);
      if (!defer) this.flush();
      else if (this.frame === null) this.frame = window.requestAnimationFrame(this.flush);
    }
    return true;
  }

  flush = () => {
    if (this.frame !== null) window.cancelAnimationFrame(this.frame);
    this.frame = null;
    const dirty = this.dirty;
    this.dirty = new Set();
    for (const id of dirty) this.rowListeners.get(id)?.forEach((listener) => listener());
  };
}
