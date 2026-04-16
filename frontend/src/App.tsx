// frontend/src/App.tsx
import { useEffect, useMemo, useState } from "react";

import {
  bootstrapAdminSession,
  clearAccessToken,
  getCurrentUser,
  listConversations,
  loadStoredAccessToken,
  setAccessToken,
} from "./api/client";
import { AdminPage } from "./pages/AdminPage";
import { LoginPage } from "./pages/LoginPage";
import { WorkbenchPage } from "./pages/WorkbenchPage";

type ConversationItem = {
  conversation_id: string;
  session_id: string;
  title: string;
};

type CurrentUser = {
  user_id: number;
  full_name: string;
  role: string;
};

export default function App() {
  const [page, setPage] = useState<"workbench" | "admin">("workbench");
  const [ready, setReady] = useState(false);
  const [authed, setAuthed] = useState(false);
  const [sessionId, setSessionId] = useState("");
  const [conversations, setConversations] = useState<ConversationItem[]>([]);
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [isEmbedMode, setIsEmbedMode] = useState(false);

  const refreshConversations = async (preferredSessionId?: string) => {
    const result = await listConversations();
    const items = ((result.data ?? []) as ConversationItem[]) || [];
    setConversations(items);

    if (preferredSessionId) {
      setSessionId(preferredSessionId);
      return items;
    }

    if (!sessionId && items.length > 0) {
      setSessionId(items[0].session_id);
    }
    return items;
  };

  useEffect(() => {
    const boot = async () => {
      const url = new URL(window.location.href);
      const embedToken = url.searchParams.get("embed_token");
      const embedSessionId = url.searchParams.get("session_id");

      if (embedToken && embedSessionId) {
        setAccessToken(embedToken, false);
        setAuthed(true);
        setIsEmbedMode(true);
        setSessionId(embedSessionId);
        await refreshConversations(embedSessionId);
        setReady(true);
        return;
      }

      const token = loadStoredAccessToken();
      if (!token) {
        setReady(true);
        return;
      }

      try {
        const profile = await getCurrentUser();
        const user = (profile ?? {}) as CurrentUser;
        setCurrentUser(user);
        setAuthed(true);

        const items = await refreshConversations();
        if (items.length === 0) {
          const created = await bootstrapAdminSession();
          const nextSessionId = String(created.data?.session_id ?? "");
          await refreshConversations(nextSessionId);
        }
      } catch {
        clearAccessToken();
        setAuthed(false);
        setCurrentUser(null);
      } finally {
        setReady(true);
      }
    };

    void boot();
  }, []);

  const handleLoggedIn = async () => {
    const profile = await getCurrentUser();
    const user = (profile ?? {}) as CurrentUser;
    setCurrentUser(user);
    setAuthed(true);

    const items = await refreshConversations();
    if (items.length === 0) {
      const created = await bootstrapAdminSession();
      const nextSessionId = String(created.data?.session_id ?? "");
      await refreshConversations(nextSessionId);
    }
  };

  const handleNewConversation = async () => {
    const created = await bootstrapAdminSession();
    const nextSessionId = String(created.data?.session_id ?? "");
    await refreshConversations(nextSessionId);
  };

  const handleLogout = () => {
    clearAccessToken();
    setAuthed(false);
    setSessionId("");
    setConversations([]);
    setCurrentUser(null);
    setIsEmbedMode(false);
    setPage("workbench");
  };

  const shell = useMemo(() => {
    if (!ready) {
      return (
        <main className="login-screen">
          <section className="login-card">
            <p>正在初始化工作台...</p>
          </section>
        </main>
      );
    }

    if (!authed) {
      return <LoginPage onLoggedIn={() => void handleLoggedIn()} />;
    }

    return (
      <div className="workspace-shell">
        <aside className="history-sidebar">
          <div className="history-sidebar__header">
            <div>
              <h2>历史会话</h2>
              {!isEmbedMode && currentUser ? (
                <p>
                  {currentUser.full_name}
                  <span>{currentUser.role}</span>
                </p>
              ) : (
                <p>嵌入工作台</p>
              )}
            </div>
            {!isEmbedMode ? (
              <div className="history-sidebar__actions">
                <button type="button" onClick={() => void handleNewConversation()}>
                  新建会话
                </button>
                <button
                  type="button"
                  className={page === "admin" ? "history-secondary is-active" : "history-secondary"}
                  onClick={() => setPage(page === "admin" ? "workbench" : "admin")}
                >
                  {page === "admin" ? "返回工作台" : "管理配置"}
                </button>
              </div>
            ) : null}
          </div>

          <div className="history-sidebar__list">
            {conversations.length === 0 ? <div className="history-empty">暂无历史会话</div> : null}
            {conversations.map((item) => (
              <button
                key={item.conversation_id}
                type="button"
                className={`history-item ${item.session_id === sessionId ? "is-active" : ""}`}
                onClick={() => setSessionId(item.session_id)}
              >
                <strong>{item.title || "新对话"}</strong>
                <span>{item.session_id}</span>
              </button>
            ))}
          </div>

          {!isEmbedMode ? (
            <div className="history-sidebar__footer">
              <button type="button" className="history-logout" onClick={handleLogout}>
                退出登录
              </button>
            </div>
          ) : null}
        </aside>

        <section className="workspace-shell__main">
          {page === "admin" && currentUser ? (
            <AdminPage role={currentUser.role} />
          ) : sessionId ? (
            <WorkbenchPage
              sessionId={sessionId}
              onSessionRefresh={() => void refreshConversations(sessionId)}
            />
          ) : null}
        </section>
      </div>
    );
  }, [authed, conversations, currentUser, isEmbedMode, page, ready, sessionId]);

  return shell;
}
