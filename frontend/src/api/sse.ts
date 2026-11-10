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

export class RunStreamInterruptedError extends Error {
  constructor(public runId: string, public afterSeq: number, cause: unknown) {
    super(cause instanceof Error ? cause.message : "运行订阅暂时中断");
  }
}

export class RunStreamHttpError extends Error {
  constructor(public status: number) { super(`恢复运行订阅失败: ${status}`); }
  get retryable() { return this.status === 408 || this.status === 429 || this.status >= 500; }
}

/** 断线只续订已经创建的 run，绝不重发创建任务的 POST。 */
export async function consumeResumableRun(
  initialBody: ReadableStream<Uint8Array> | undefined,
  resume: (runId: string, afterSeq: number, signal?: AbortSignal) => Promise<ReadableStream<Uint8Array>>,
  onEvent: (event: Record<string, unknown>) => void,
  options: { sessionId?: string; runId?: string; afterSeq?: number; signal?: AbortSignal; retryDelayMs?: number; retryWindowMs?: number;
    allowInitialSequenceOffset?: boolean;
    onTransportStatus?: (state: "reconnecting" | "connected" | "stopped") => void } = {},
) {
  let body: ReadableStream<Uint8Array> | undefined = initialBody;
  let runId = options.runId;
  let cursor = options.afterSeq ?? 0;
  let initialCursorKnown = options.afterSeq !== undefined || !!options.runId;
  let completed = false;
  let callbackFailed = false;
  let outageStartedAt: number | undefined;
  let reconnecting = false;
  let legacyAnswerAcknowledged = false;
  const transportStatus = (state: "reconnecting" | "connected" | "stopped") => {
    try { options.onTransportStatus?.(state); }
    catch (error) { callbackFailed = true; throw error; }
  };
  try {
    for (let attempt = 0; ; attempt++) {
      options.signal?.throwIfAborted();
      let releaseConnection = () => {};
      try {
        if (!body) {
          const controller = new AbortController();
          const abort = () => controller.abort(options.signal?.reason);
          options.signal?.addEventListener("abort", abort, { once: true });
          releaseConnection = () => options.signal?.removeEventListener("abort", abort);
          if (options.signal?.aborted) abort();
          const remaining = (options.retryWindowMs ?? 120_000) - (outageStartedAt === undefined ? 0 : Date.now() - outageStartedAt);
          const timer = setTimeout(() => controller.abort(), Math.min(10_000, Math.max(1, remaining)));
          try { body = await resume(runId!, cursor, controller.signal); }
          finally { clearTimeout(timer); }
        }
        await consumeEventStream(body, (event) => {
          if (options.sessionId && event.session_id && event.session_id !== options.sessionId) throw new ProtocolStreamError("运行事件属于其他会话");
          const payload = event.payload as Record<string, unknown> | undefined;
          const incoming = typeof event.run_id === "string" ? event.run_id : event.type === "run_started" && typeof payload?.run_id === "string" ? payload.run_id : undefined;
          if (incoming && runId && incoming !== runId) throw new ProtocolStreamError("运行事件标识不匹配");
          runId = incoming ?? runId;
          if (!initialCursorKnown && initialBody === body && event.type === "session_created"
              && typeof payload?.resume_after_seq === "number" && Number.isSafeInteger(payload.resume_after_seq)
              && payload.resume_after_seq >= 0 && runId) {
            cursor = payload.resume_after_seq;
            initialCursorKnown = true;
          }
          if (typeof event.seq === "number") {
            if (!initialCursorKnown && initialBody === body && options.allowInitialSequenceOffset && runId) {
              cursor = Math.max(0, event.seq - 1);
            }
            initialCursorKnown = true;
            if (event.seq <= cursor) return;
            if (event.seq !== cursor + 1) throw new EventGap("运行事件缺少连续片段");
          }
          if (options.allowInitialSequenceOffset && event.type === "ask_resolved" && payload?.resume_message === "") {
            legacyAnswerAcknowledged = true;
          }
          try { onEvent(event); } catch (error) { callbackFailed = true; throw error; }
          if (typeof event.seq === "number") {
            cursor = event.seq;
            outageStartedAt = undefined;
            attempt = 0;
            if (reconnecting) { reconnecting = false; transportStatus("connected"); }
          }
          if ((event.type === "completed" && !payload?.historical_pause) || event.type === "stream_end"
              || (event.type === "ask_resolved" && payload?.no_run === true)
              || (event.type === "error" && event.seq == null)) completed = true;
          if (completed) return false;
        });
        if (completed || (!runId && legacyAnswerAcknowledged)) return;
        throw new EventGap("运行流在终态前结束");
      } catch (error) {
        if (callbackFailed || options.signal?.aborted || error instanceof SyntaxError || error instanceof ProtocolStreamError
            || (error instanceof RunStreamHttpError && !error.retryable) || !runId) throw error;
        outageStartedAt ??= Date.now();
        const remaining = (options.retryWindowMs ?? 120_000) - (Date.now() - outageStartedAt);
        if (remaining <= 0) throw new RunStreamInterruptedError(runId, cursor, error);
        if (!reconnecting) { reconnecting = true; transportStatus("reconnecting"); }
        const delay = Math.min(remaining, options.retryDelayMs === undefined
          ? Math.min(5000, 500 * 2 ** Math.min(attempt, 4)) * (0.8 + Math.random() * 0.4)
          : options.retryDelayMs);
        await new Promise<void>((resolve, reject) => {
          const done = () => { options.signal?.removeEventListener("abort", abort); resolve(); };
          const timer = setTimeout(done, delay);
          const abort = () => { clearTimeout(timer); reject(options.signal?.reason ?? new DOMException("Aborted", "AbortError")); };
          options.signal?.addEventListener("abort", abort, { once: true });
          if (options.signal?.aborted) abort();
        });
        body = undefined;
      } finally {
        releaseConnection();
      }
    }
  } finally {
    if (reconnecting && !callbackFailed) transportStatus("stopped");
  }
}
