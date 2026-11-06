type RefreshReceipt = { revision: number; added: number; updated: number; removed: number };

export function requestHostToolRefresh(origin: string, sessionId: string, revision: number): Promise<RefreshReceipt> {
  const requestId = Array.from(crypto.getRandomValues(new Uint32Array(4))).join("-");
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      window.clearTimeout(timer);
      window.removeEventListener("message", onReply);
    };
    const onReply = (event: MessageEvent) => {
      if (event.source !== window.parent || event.origin !== origin) return;
      const data = event.data;
      if (data?.source !== "aethercore-host" || data.type !== "aethercore:refresh-tools-result") return;
      const result = data.payload;
      if (result?.request_id !== requestId || result.session_id !== sessionId) return;
      cleanup();
      if (typeof result.error === "string") {
        reject(new Error(result.error));
      } else if (
        [result.revision, result.added, result.updated, result.removed].every((value) => Number.isSafeInteger(value) && value >= 0)
        && result.revision >= revision
      ) {
        resolve(result as RefreshReceipt);
      } else {
        reject(new Error("宿主返回的工具目录回执无效。"));
      }
    };
    const timer = window.setTimeout(() => {
      cleanup();
      reject(new Error("等待宿主刷新工具目录超时，请稍后重试。"));
    }, 20_000);
    window.addEventListener("message", onReply);
    try {
      window.parent.postMessage({
        source: "aethercore-workbench",
        type: "aethercore:refresh-tools",
        payload: { request_id: requestId, session_id: sessionId, revision },
      }, origin);
    } catch (error) {
      cleanup();
      reject(error);
    }
  });
}
