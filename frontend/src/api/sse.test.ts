import { describe, expect, it } from "vitest";
import { SseDecoder, consumeEventStream, consumeResumableRun } from "./sse";

describe("SSE framing", () => {
  it("preserves multi-line JSON through every character boundary", () => {
    const input = ': heartbeat\r\ndata: {"type":"content_delta",\r\ndata: "text":"中文 192.0.2.10"}\r\n\r\n';
    for (let index = 0; index <= input.length; index++) {
      const decoder = new SseDecoder();
      expect([...decoder.feed(input.slice(0, index)), ...decoder.feed(input.slice(index), true)])
        .toEqual([{ type: "content_delta", text: "中文 192.0.2.10" }]);
    }
  });
  it("handles a final complete frame and rejects malformed data", () => {
    expect(new SseDecoder().feed('data: {"seq":1}', true)).toEqual([{ seq: 1 }]);
    expect(() => new SseDecoder().feed('data: {broken}\n\n')).toThrow();
  });
  it("accepts CR-only frame boundaries at every split", () => {
    const input = 'data: {"seq":1}\r\rdata: {"seq":2}\r\r';
    for (let index = 0; index <= input.length; index++) {
      const decoder = new SseDecoder();
      expect([...decoder.feed(input.slice(0, index)), ...decoder.feed(input.slice(index), true)]).toEqual([{ seq: 1 }, { seq: 2 }]);
    }
  });
});

const eventBody = (events: Record<string, unknown>[]) => new ReadableStream<Uint8Array>({ start(controller) {
  const text = events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("");
  const bytes = new TextEncoder().encode(text);
  for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
  controller.close();
} });

describe("durable run replay", () => {
  it("stops at the durable terminal without waiting for HTTP EOF or replaying the tail", async () => {
    let cancelled = false;
    const body = new ReadableStream<Uint8Array>({ start(controller) {
      controller.enqueue(new TextEncoder().encode('data: {"run_id":"r","seq":1,"type":"completed"}\n\n'));
    }, cancel() { cancelled = true; } });
    const seen: unknown[] = [];
    await consumeResumableRun(body, async () => { throw new Error("terminal must not reconnect"); }, (event) => { seen.push(event.seq); });
    expect(seen).toEqual([1]);
    expect(cancelled).toBe(true);
  });
  it("decodes Chinese across every UTF-8 byte boundary", async () => {
    const events: Record<string, unknown>[] = [];
    await consumeEventStream(eventBody([{ text: "中文 192.0.2.10" }]), (event) => events.push(event));
    expect(events).toEqual([{ text: "中文 192.0.2.10" }]);
  });
  it("fills a sequence gap, deduplicates replay, and never resubmits a task", async () => {
    const events: number[] = [];
    const resumes: unknown[] = [];
    const event = (seq: number, type = "content_delta") => ({ run_id: "run", session_id: "session", seq, type });
    await consumeResumableRun(eventBody([event(1), event(3)]), async (id, cursor) => {
      resumes.push([id, cursor]);
      return eventBody([event(1), event(2), event(3, "completed")]);
    }, (item) => { events.push(item.seq as number); }, { sessionId: "session", retryDelayMs: 0 });
    expect(events).toEqual([1, 2, 3]);
    expect(resumes).toEqual([["run", 1]]);
  });
  it("resumes after the snapshot cursor without duplicating its text", async () => {
    const seen: number[] = [];
    await consumeResumableRun(eventBody([{ run_id: "run", seq: 10 }, { run_id: "run", seq: 11, type: "completed" }]),
      async () => { throw new Error("should not reconnect"); }, (item) => { seen.push(item.seq as number); },
      { runId: "run", afterSeq: 10 });
    expect(seen).toEqual([11]);
  });
  it("cancels retry wait and does not retry reducer errors", async () => {
    const abort = new AbortController();
    const pending = consumeResumableRun(eventBody([{ run_id: "run", seq: 1 }]), async () => { throw new Error("unexpected resume"); },
      () => { abort.abort(); }, { signal: abort.signal, retryDelayMs: 0 });
    await expect(pending).rejects.toBeDefined();
    await expect(consumeResumableRun(eventBody([{ run_id: "run", seq: 1 }]), async () => { throw new Error("unexpected resume"); },
      () => { throw new Error("reducer failed"); }, { retryDelayMs: 0 })).rejects.toThrow("reducer failed");
  });
});
