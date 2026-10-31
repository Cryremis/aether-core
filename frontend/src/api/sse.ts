class ProtocolStreamError extends Error {}

/** SSE 帧跨网络包归并；data 多行、UTF-8 与尾帧由同一实现处理。 */
export class SseDecoder {
  private buffer = "";
  private lines: string[] = [];
  private frameSize = 0;
  feed(text: string, final = false): Record<string, unknown>[] {
    this.buffer += text;
    if (this.buffer.length + this.frameSize > 8 * 1024 * 1024) throw new ProtocolStreamError("SSE 数据帧过大");
    const result: Record<string, unknown>[] = [];
    while (true) {
      const match = /\r\n|\r|\n/.exec(this.buffer);
      if (!match) break;
      if (!final && match[0] === "\r" && match.index === this.buffer.length - 1) break;
      const line = this.buffer.slice(0, match.index);
      this.buffer = this.buffer.slice(match.index + match[0].length);
      if (line) {
        this.lines.push(line);
        this.frameSize += line.length + 1;
      } else {
        this.parse(this.lines.join("\n"), result);
        this.lines = [];
        this.frameSize = 0;
      }
    }
    if (final) {
      if (this.buffer) this.lines.push(this.buffer);
      this.parse(this.lines.join("\n"), result);
      this.buffer = "";
      this.lines = [];
      this.frameSize = 0;
    }
    return result;
  }
  private parse(frame: string, output: Record<string, unknown>[]) {
    const data = frame.split(/\r?\n/).filter((line) => line.startsWith("data:")).map((line) => line.slice(5).replace(/^ /, "")).join("\n");
    if (data && data !== "[DONE]") {
      const value = JSON.parse(data);
      if (!value || typeof value !== "object" || Array.isArray(value)) throw new ProtocolStreamError("无效的 SSE 事件");
      output.push(value);
    }
  }
}

export async function consumeEventStream(body: ReadableStream<Uint8Array>, onEvent: (event: Record<string, unknown>) => unknown) {
  const reader = body.getReader();
  const text = new TextDecoder("utf-8", { fatal: true });
  const decoder = new SseDecoder();
  try {
    while (true) {
      const { done, value } = await reader.read();
      for (const event of decoder.feed(done ? text.decode() : text.decode(value, { stream: true }), done)) {
        if (onEvent(event) === false) return;
      }
      if (done) return;
    }
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

class EventGap extends Error {}

/** 断线只续订已经创建的 run，绝不重发创建任务的 POST。 */
export async function consumeResumableRun(
  initialBody: ReadableStream<Uint8Array>,
  resume: (runId: string, afterSeq: number) => Promise<ReadableStream<Uint8Array>>,
  onEvent: (event: Record<string, unknown>) => void,
  options: { sessionId?: string; runId?: string; afterSeq?: number; signal?: AbortSignal; retryDelayMs?: number } = {},
) {
  let body: ReadableStream<Uint8Array> | undefined = initialBody;
  let runId = options.runId;
  let cursor = options.afterSeq ?? 0;
  let completed = false;
  let callbackFailed = false;
  for (let attempt = 0; ; attempt++) {
    options.signal?.throwIfAborted();
    try {
      if (!body) body = await resume(runId!, cursor);
      await consumeEventStream(body, (event) => {
        if (options.sessionId && event.session_id && event.session_id !== options.sessionId) throw new ProtocolStreamError("运行事件属于其他会话");
        const payload = event.payload as Record<string, unknown> | undefined;
        const incoming = typeof event.run_id === "string" ? event.run_id : event.type === "run_started" && typeof payload?.run_id === "string" ? payload.run_id : undefined;
        if (incoming && runId && incoming !== runId) throw new ProtocolStreamError("运行事件标识不匹配");
        runId = incoming ?? runId;
        if (typeof event.seq === "number") {
          if (event.seq <= cursor) return;
          if (event.seq !== cursor + 1) throw new EventGap("运行事件缺少连续片段");
        }
        try { onEvent(event); } catch (error) { callbackFailed = true; throw error; }
        if (typeof event.seq === "number") cursor = event.seq;
        if (event.type === "completed" || (event.type === "error" && event.seq == null)) completed = true;
        if (completed) return false;
      });
      if (completed) return;
      throw new EventGap("运行流在终态前结束");
    } catch (error) {
      if (callbackFailed || options.signal?.aborted || error instanceof SyntaxError || error instanceof ProtocolStreamError || !runId || attempt >= 3) throw error;
      await new Promise<void>((resolve, reject) => {
        const done = () => { options.signal?.removeEventListener("abort", abort); resolve(); };
        const timer = setTimeout(done, (options.retryDelayMs ?? 500) * (attempt + 1));
        const abort = () => { clearTimeout(timer); reject(options.signal?.reason ?? new DOMException("Aborted", "AbortError")); };
        options.signal?.addEventListener("abort", abort, { once: true });
      });
      body = undefined;
    }
  }
}
