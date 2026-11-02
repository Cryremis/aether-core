import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import {
  getAdminConversationDetail, listAdminConversations,
  type AuditConversationDetail, type AuditConversationPage, type AuditConversationQuery,
} from "../../api/client";
import { ChatTimeline } from "../workbench/ChatTimeline";
import { convertAuditDetailToChatMessages, getRuntimeStatusClass, getRuntimeStatusLabel } from "./auditMessages";

const MESSAGE_PAGE_SIZE = 30;
const initialQuery: AuditConversationQuery = {
  limit: 50, offset: 0, q: "", sort_by: "last_message_at", sort_order: "desc", time_field: "last_message_at",
};

function formatTime(value?: string | null) {
  if (!value) return "未记录";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

function ownerLabel(item: AuditConversationDetail["audit"]) {
  return item.owner_user_name || item.external_user_name || item.external_user_id || "未知用户";
}

export function AuditBrowser({ platformId }: { platformId?: number }) {
  const [query, setQuery] = useState(initialQuery);
  const [search, setSearch] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [page, setPage] = useState<AuditConversationPage>({ items: [], total: 0, limit: 50, offset: 0 });
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<AuditConversationDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [messageBefore, setMessageBefore] = useState<number | undefined>();
  const [detailRefresh, setDetailRefresh] = useState(0);
  const timelineRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    setListLoading(true);
    setListError("");
    void listAdminConversations(platformId, query, controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return;
        const next = result.data as AuditConversationPage;
        if (query.offset > 0 && query.offset >= next.total) {
          setQuery((current) => ({ ...current, offset: Math.max(0, Math.ceil(next.total / current.limit) - 1) * current.limit }));
          return;
        }
        setPage(next);
      })
      .catch((err) => {
        if (!controller.signal.aborted) setListError(err instanceof Error ? err.message : "加载会话列表失败");
      })
      .finally(() => { if (!controller.signal.aborted) setListLoading(false); });
    return () => controller.abort();
  }, [platformId, query, refresh]);

  useEffect(() => {
    if (!selectedId) return;
    const controller = new AbortController();
    setDetailLoading(true);
    setDetailError("");
    // 切换或翻页时立即卸载旧时间线，避免旧内容被误认为新会话。
    setDetail(null);
    void getAdminConversationDetail(selectedId, { limit: MESSAGE_PAGE_SIZE, before: messageBefore }, controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) {
          setDetail(result.data as AuditConversationDetail);
          if (timelineRef.current) timelineRef.current.scrollTop = 0;
        }
      })
      .catch((err) => {
        if (!controller.signal.aborted) setDetailError(err instanceof Error ? err.message : "加载会话详情失败");
      })
      .finally(() => { if (!controller.signal.aborted) setDetailLoading(false); });
    return () => controller.abort();
  }, [selectedId, messageBefore, detailRefresh]);

  const messages = useMemo(() => convertAuditDetailToChatMessages(detail), [detail]);
  const messagePage = detail?.transcript_page;
  const updateQuery = (patch: Partial<AuditConversationQuery>) => setQuery((current) => ({ ...current, ...patch, offset: 0 }));

  const applyFilters = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    // 日期按浏览器本地时区解释，结束日期包含当天；跨月与夏令时交给 Date 处理。
    const start = dateFrom ? new Date(`${dateFrom}T00:00:00`) : null;
    const end = dateTo ? new Date(`${dateTo}T00:00:00`) : null;
    if (end) end.setDate(end.getDate() + 1);
    updateQuery({ q: search.trim(), time_from: start?.toISOString(), time_to: end?.toISOString() });
  };

  const selectSession = (sessionId: string) => {
    if (sessionId === selectedId) return;
    setDetail(null);
    setDetailLoading(true);
    setMessageBefore(undefined);
    setSelectedId(sessionId);
  };

  return (
    <div className="audit-browser">
      <form className="audit-browser__toolbar" onSubmit={applyFilters}>
        <div className="audit-browser__search">
          <input aria-label="搜索审计会话" type="search" maxLength={200} placeholder="搜索标题、用户名、用户 ID、会话 ID" value={search} onChange={(event) => setSearch(event.target.value)} />
          <button type="submit" className="action-button action-button--ghost">搜索 / 应用</button>
          <button type="button" className="action-button action-button--ghost" onClick={() => {
            setSearch(""); setDateFrom(""); setDateTo(""); setQuery({ ...initialQuery });
          }}>重置</button>
        </div>
        <div className="audit-browser__filters">
          <label>排序
            <select aria-label="排序字段" value={query.sort_by} onChange={(event) => updateQuery({ sort_by: event.target.value as AuditConversationQuery["sort_by"] })}>
              <option value="last_message_at">最后消息时间</option><option value="created_at">创建时间</option>
              <option value="updated_at">更新时间</option><option value="message_count">消息数量</option>
            </select>
          </label>
          <select aria-label="排序方向" value={query.sort_order} onChange={(event) => updateQuery({ sort_order: event.target.value as "asc" | "desc" })}>
            <option value="desc">降序</option><option value="asc">升序</option>
          </select>
          <label>时间范围
            <select aria-label="时间筛选字段" value={query.time_field} onChange={(event) => updateQuery({ time_field: event.target.value as AuditConversationQuery["time_field"] })}>
              <option value="last_message_at">最后消息时间</option><option value="created_at">创建时间</option><option value="updated_at">更新时间</option>
            </select>
          </label>
          <input aria-label="开始日期" type="date" value={dateFrom} max={dateTo || undefined} onChange={(event) => setDateFrom(event.target.value)} />
          <span>至</span>
          <input aria-label="结束日期" type="date" value={dateTo} min={dateFrom || undefined} onChange={(event) => setDateTo(event.target.value)} />
        </div>
        <span className="audit-browser__hint">搜索会话与用户信息；日期范围包含结束当天，点击“搜索 / 应用”生效。</span>
      </form>
      <div className="audit-browser__layout">
        <div className="audit-browser__list" aria-busy={listLoading}>
          <div className="audit-browser__list-head">
            <strong>会话列表 · {page.total}</strong>
            <button type="button" className="action-button action-button--ghost small" disabled={listLoading} onClick={() => setRefresh((value) => value + 1)}>{listLoading ? "加载中…" : "刷新列表"}</button>
          </div>
          {listError ? <div role="alert" className="platforms-error">{listError}</div> : null}
          <div className="audit-browser__columns"><span>对话 / 用户</span><span>消息</span><span>最后消息 / 创建时间</span></div>
          <div className="audit-browser__rows">
            {!listError && listLoading ? <div role="status" className="admin-panel__empty">正在加载会话列表…</div> : null}
            {!listLoading && !listError && page.items.length === 0 ? <div className="admin-panel__empty">没有符合条件的会话</div> : null}
            {!listLoading && !listError && page.items.map((item) => (
              <button key={item.session_id} type="button" aria-pressed={selectedId === item.session_id} className={`audit-browser__row ${selectedId === item.session_id ? "is-active" : ""}`} onClick={() => selectSession(item.session_id)}>
                <span className="audit-browser__identity"><strong title={`${item.title}\nSession: ${item.session_id}`}>{item.title || "新对话"}</strong><span title={`${ownerLabel(item)} · ${item.external_user_id || item.owner_user_id || ""}`}>{ownerLabel(item)}</span></span>
                <span>{item.message_count}</span>
                <span className="audit-browser__dates"><time title="最后消息时间">{formatTime(item.last_message_at)}</time><time title="创建时间">{formatTime(item.created_at)}</time></span>
              </button>
            ))}
          </div>
          <div className="audit-browser__pagination">
            <select aria-label="每页会话数" value={query.limit} onChange={(event) => updateQuery({ limit: Number(event.target.value) })}>
              <option value={25}>25 条 / 页</option><option value={50}>50 条 / 页</option><option value={100}>100 条 / 页</option>
            </select>
            <span>{page.total ? Math.floor(page.offset / page.limit) + 1 : 0} / {Math.ceil(page.total / page.limit)} 页</span>
            <button type="button" disabled={listLoading || page.offset === 0} onClick={() => setQuery((current) => ({ ...current, offset: Math.max(0, current.offset - current.limit) }))}>上一页</button>
            <button type="button" disabled={listLoading || page.offset + page.limit >= page.total} onClick={() => setQuery((current) => ({ ...current, offset: current.offset + current.limit }))}>下一页</button>
          </div>
        </div>
        <div className="audit-browser__detail" aria-busy={detailLoading}>
          <div className="audit-browser__list-head">
            <strong>会话详情</strong>
            <button type="button" className="action-button action-button--ghost small" disabled={!selectedId || detailLoading} onClick={() => setDetailRefresh((value) => value + 1)}>刷新消息</button>
          </div>
          {detailError ? <div className="platforms-error" role="alert">{detailError}<button type="button" onClick={() => setDetailRefresh((value) => value + 1)}>重试</button></div> : null}
          {detailLoading ? <div role="status" className="admin-panel__empty">正在加载会话消息…</div> : null}
          {!detailLoading && !detailError && !detail ? <div className="admin-panel__empty">选择左侧会话查看审计详情，默认显示最近 {MESSAGE_PAGE_SIZE} 条消息。</div> : null}
          {detail ? <>
            <div className="audit-browser__detail-header">
              <strong>{detail.audit.title || "新对话"}</strong>
              <div className="management-console__audit-detail-meta">
                <span>用户：{ownerLabel(detail.audit)}</span><span>{detail.message_count} 条消息</span>
                <span>平台：{detail.audit.platform_display_name || detail.host_name}</span>
                <span>创建：{formatTime(detail.created_at)}</span><span>最后消息：{formatTime(detail.audit.last_message_at)}</span>
                <span>更新：{formatTime(detail.audit.updated_at)}</span><span>网络：{detail.allow_network ? "允许" : "受限"}</span>
                <span className={`runtime-status runtime-status--${detail.runtime ? getRuntimeStatusClass(detail.runtime.status) : "none"}`}>Runtime：{detail.runtime ? getRuntimeStatusLabel(detail.runtime.status) : "尚未创建"}</span>
                <span className="audit-browser__session-id">Session：{detail.session_id}</span>
              </div>
            </div>
            <div className="audit-browser__message-nav">
              <span>{messagePage && messagePage.total ? `${messagePage.start + 1}–${messagePage.end} / ${messagePage.total} 条` : "暂无消息"}</span>
              <button type="button" disabled={!messagePage?.has_more} onClick={() => setMessageBefore(messagePage?.start)}>更早消息</button>
              <button type="button" disabled={!messagePage || messagePage.end >= messagePage.total} onClick={() => setMessageBefore(Math.min(messagePage!.total, messagePage!.end + MESSAGE_PAGE_SIZE))}>更新消息</button>
              <button type="button" disabled={messageBefore === undefined} onClick={() => setMessageBefore(undefined)}>回到最新</button>
            </div>
            <div ref={timelineRef} className="audit-browser__timeline">
              <ChatTimeline key={`${detail.session_id}-${messagePage?.start}`} loading={false} messages={messages} actionsDisabled={true} showMessageTimestamps={true} />
            </div>
          </> : null}
        </div>
      </div>
    </div>
  );
}
