import { useEffect, useState } from "react";
import { cancelCrossChatDelivery, listCrossChatDeliveries } from "../../api/client";

const deliveryLabels: Record<string, string> = {
  awaiting_approval: "等待确认", queued: "已排队", dispatching: "投递中", delivered: "已投递",
  declined: "未批准", cancelled: "已撤销", expired: "已过期", failed: "投递失败",
};
const runLabels: Record<string, string> = {
  running: "运行中", queued: "等待启动", waiting_input: "等待用户回答", completed: "已完成",
  failed: "运行失败", cancelled: "运行已停止", timed_out: "运行超时",
};
const modeLabels = { start: "立即开始", queue: "空闲后开始", steer: "原运行追加" };

export function CrossChatDeliveriesDialog({ sessionId, onClose, onOpenSession }: {
  sessionId: string;
  onClose: () => void;
  onOpenSession?: (id: string) => void;
}) {
  const [cursor, setCursor] = useState<string | null>(null);
  const [page, setPage] = useState<Awaited<ReturnType<typeof listCrossChatDeliveries>> | null>(null);
  const [revision, setRevision] = useState(0);
  const [cancelling, setCancelling] = useState<string | null>(null);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    let inFlight = false;
    const load = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const result = await listCrossChatDeliveries(sessionId, cursor, controller.signal);
        if (!controller.signal.aborted) {
          setPage(result);
          setLoadError("");
        }
      } catch (err) {
        if (!controller.signal.aborted) setLoadError(err instanceof Error ? err.message : "读取投递失败");
      } finally {
        inFlight = false;
      }
    };
    void load();
    const timer = cursor ? undefined : window.setInterval(() => { if (!document.hidden) void load(); }, 5000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [sessionId, cursor, revision]);

  const cancel = async (deliveryId: string) => {
    setCancelling(deliveryId);
    setActionError("");
    try {
      await cancelCrossChatDelivery(sessionId, deliveryId);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "撤销投递失败");
    } finally {
      setCancelling(null);
      setRevision((value) => value + 1);
    }
  };

  const changePage = (next: string | null) => {
    setPage(null);
    setLoadError("");
    setActionError("");
    setCursor(next);
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="llm-dialog settings-dialog session-inspection" role="dialog" aria-modal="true"
        aria-label="跨会话投递" onClick={(event) => event.stopPropagation()}
        onKeyDown={(event) => { if (event.key === "Escape") onClose(); }}>
        <div className="settings-dialog__header">
          <h3>跨会话投递</h3>
          <button type="button" className="icon-button subtle" aria-label="关闭" autoFocus onClick={onClose}>×</button>
        </div>
        <div className="session-inspection__body">
          {(actionError || loadError) && <p role="alert">{actionError || loadError}</p>}
          {!page && !loadError && <p role="status">正在读取投递记录…</p>}
          {page?.items.length === 0 && <p>本页没有投递记录。</p>}
          <div className="cross-chat-receipts">
            {page?.items.map((item) => (
              <article key={item.delivery_id}>
                <strong>{item.direction === "sent" ? "发往" : "来自"} {item.peer_title ?? "不可访问的会话"}</strong>
                <p>{modeLabels[item.mode]} · {deliveryLabels[item.status] ?? item.status}
                  {item.run_status && ` · ${runLabels[item.run_status] ?? item.run_status}`}</p>
                {item.detail && <p>{item.detail}</p>}
                <details><summary>查看正文</summary><pre>{item.message}</pre></details>
                <div className="cross-chat-receipts__actions">
                  {item.peer_session_id && <button type="button" className="btn secondary" disabled={!onOpenSession}
                    onClick={() => onOpenSession?.(item.peer_session_id!)}>打开会话</button>}
                  {item.direction === "sent" && item.status === "queued" &&
                    <button type="button" className="btn secondary" disabled={cancelling !== null}
                      onClick={() => void cancel(item.delivery_id)}>撤销排队</button>}
                </div>
              </article>
            ))}
          </div>
          <div className="cross-chat-receipts__actions">
            <button type="button" className="btn secondary" onClick={() => { setActionError(""); setRevision((value) => value + 1); }}>刷新</button>
            {cursor && <button type="button" className="btn secondary" onClick={() => changePage(null)}>最新</button>}
            {page?.next_cursor && <button type="button" className="btn secondary" onClick={() => changePage(page.next_cursor)}>更早</button>}
          </div>
        </div>
      </div>
    </div>
  );
}
