// frontend/src/App.tsx
import { Suspense, lazy, useEffect, useMemo, useState } from "react";

import {
  bootstrapAdminSession,
  clearAccessToken,
  deleteSession,
  getCurrentUser,
  listConversations,
  loadStoredAccessToken,
  renameSession,
  setAccessToken,
} from "./api/client";
import { LoginPage } from "./pages/LoginPage";

const AdminPage = lazy(async () => import("./pages/AdminPage").then((module) => ({ default: module.AdminPage })));
const WorkbenchPage = lazy(async () => import("./pages/WorkbenchPage").then((module) => ({ default: module.WorkbenchPage })));

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
  const [isNewSession, setIsNewSession] = useState(false);

  const refreshConversations = async (preferredSessionId?: string) => {
    const result = await listConversations();
    const items = ((result.data ?? []) as ConversationItem[]) || [];
    setConversations(items);

    if (preferredSessionId) {
      setSessionId(preferredSessionId);
      setIsNewSession(false);
      return items;
    }

    if (!sessionId && items.length > 0) {
      setSessionId(items[0].session_id);
      setIsNewSession(false);
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

  const handleNewConversation = () => {
    setSessionId("");
    setIsNewSession(true);
  };

  const handleDeleteSession = async (targetSessionId: string) => {
    if (!window.confirm("确定删除该会话吗？")) return;
    try {
      await deleteSession(targetSessionId);
      await refreshConversations();
      if (sessionId === targetSessionId) {
        handleNewConversation();
      }
    } catch (err) {
      console.error("删除会话失败:", err);
    }
  };

  const handleRenameSession = async (targetSessionId: string, currentTitle: string) => {
    const newTitle = window.prompt("请输入新的会话名称:", currentTitle || "新对话");
    if (!newTitle || newTitle.trim() === "" || newTitle === currentTitle) return;
    try {
      await renameSession(targetSessionId, newTitle.trim());
      await refreshConversations(sessionId);
    } catch (err) {
      console.error("重命名会话失败:", err);
    }
  };

  const handleCreateSessionAndSelect = (newSessionId: string) => {
    setIsNewSession(false);
    setSessionId(newSessionId);
    void refreshConversations(newSessionId);
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
    const pageFallback = (
      <main className="login-screen">
        <section className="login-card">
          <p>正在加载界面...</p>
        </section>
      </main>
    );

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
        <section className="workspace-shell__main">
          <Suspense fallback={pageFallback}>
            {page === "admin" && currentUser ? (
              <AdminPage role={currentUser.role} onBack={() => setPage("workbench")} />
            ) : sessionId || isNewSession ? (
              <WorkbenchPage
                conversations={conversations}
                currentUser={currentUser}
                isEmbedMode={isEmbedMode}
                sessionId={sessionId}
                isNewSession={isNewSession}
                onAdminToggle={() => setPage("admin")}
                onLogout={handleLogout}
                onNewConversation={handleNewConversation}
                onDeleteSession={(id) => void handleDeleteSession(id)}
                onRenameSession={(id, title) => void handleRenameSession(id, title)}
                onSessionCreated={handleCreateSessionAndSelect}
                onSessionRefresh={() => void refreshConversations(sessionId)}
                onSessionSelect={(id) => { setSessionId(id); setIsNewSession(false); }}
              />
            ) : null}
          </Suspense>
        </section>
      </div>
    );
  }, [authed, conversations, currentUser, isEmbedMode, isNewSession, page, ready, sessionId]);

  return shell;
}
