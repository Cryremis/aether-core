// frontend/src/App.tsx
import { Suspense, lazy, useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import {
  bootstrapAdminSession,
  clearAccessToken,
  type CurrentUserProfile,
  deleteSession,
  getCurrentUser,
  listConversations,
  loadStoredAccessToken,
  renameSession,
  setAccessToken,
} from "./api/client";
import { AppChrome } from "./components/AppChrome";
import { AuthModal } from "./components/AuthModal";
import { PersonalSettingsDialog } from "./components/workbench/PersonalSettingsDialog";
import { HomePage } from "./pages/HomePage";
import { PlatformDetailPage } from "./pages/PlatformDetailPage";
import { PlatformsPage } from "./pages/PlatformsPage";
import { AdminPage } from "./pages/AdminPage";

const WorkbenchPage = lazy(async () => import("./pages/WorkbenchPage").then((module) => ({ default: module.WorkbenchPage })));

type ConversationItem = {
  conversation_id: string;
  session_id: string;
  title: string;
};

const EMBED_NAVIGATION_EVENT = "aethercore:session-changed";

export default function App() {
  const location = useLocation();
  const navigate = useNavigate();
  const [ready, setReady] = useState(false);
  const [authed, setAuthed] = useState(false);
  const [sessionId, setSessionId] = useState("");
  const [conversations, setConversations] = useState<ConversationItem[]>([]);
  const [currentUser, setCurrentUser] = useState<CurrentUserProfile | null>(null);
  const [isEmbedMode, setIsEmbedMode] = useState(false);
  const [isNewSession, setIsNewSession] = useState(false);
  const [authModalOpen, setAuthModalOpen] = useState(false);
  const [pendingPath, setPendingPath] = useState("/workbench");
  const [showPersonalSettingsDialog, setShowPersonalSettingsDialog] = useState(false);

  const updateWorkbenchQuery = (nextSessionId: string, nextIsNewSession: boolean) => {
    const params = new URLSearchParams(new URL(window.location.href).searchParams);
    if (nextIsNewSession || !nextSessionId) {
      params.delete("session_id");
      params.set("new", "1");
    } else {
      params.set("session_id", nextSessionId);
      params.delete("new");
    }
    const query = params.toString();
    navigate({ pathname: "/workbench", search: query ? `?${query}` : "" }, { replace: true });
  };

  const refreshConversations = async (preferredSessionId?: string) => {
    const result = await listConversations();
    const items = ((result.data ?? []) as ConversationItem[]) || [];
    setConversations(items);

    if (preferredSessionId) {
      setSessionId(preferredSessionId);
      setIsNewSession(false);
      updateWorkbenchQuery(preferredSessionId, false);
      return items;
    }

    const url = new URL(window.location.href);
    const requestedSessionId = (url.searchParams.get("session_id") || "").trim();
    const requestedNewSession = url.searchParams.get("new") === "1";

    if (requestedNewSession) {
      setSessionId("");
      setIsNewSession(true);
      return items;
    }

    if (requestedSessionId && items.some((item) => item.session_id === requestedSessionId)) {
      setSessionId(requestedSessionId);
      setIsNewSession(false);
      return items;
    }

    if (items.length > 0) {
      setSessionId(items[0].session_id);
      setIsNewSession(false);
      updateWorkbenchQuery(items[0].session_id, false);
    } else {
      setSessionId("");
      setIsNewSession(true);
      updateWorkbenchQuery("", true);
    }
    return items;
  };

  const ensureWorkbenchSession = async () => {
    const items = await refreshConversations();
    if (items.length === 0) {
      const created = await bootstrapAdminSession();
      const nextSessionId = String(created.data?.session_id ?? "");
      await refreshConversations(nextSessionId);
    }
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
        setIsNewSession(false);
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
        setCurrentUser(profile);
        setAuthed(true);
        if (window.location.pathname.startsWith("/workbench")) {
          await ensureWorkbenchSession();
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

  useEffect(() => {
    if (!authed || !location.pathname.startsWith("/workbench") || isEmbedMode) return;
    void ensureWorkbenchSession();
  }, [authed, location.pathname]);

  useEffect(() => {
    if (authed || isEmbedMode) return;
    const isProtectedPath =
      location.pathname.startsWith("/workbench") ||
      location.pathname.startsWith("/platforms") ||
      location.pathname.startsWith("/system") ||
      location.pathname.startsWith("/admin");
    if (!isProtectedPath) return;
    setPendingPath(`${location.pathname}${location.search}`);
    setAuthModalOpen(true);
    navigate("/", { replace: true });
  }, [authed, isEmbedMode, location.pathname, location.search, navigate]);

  useEffect(() => {
    if (!isEmbedMode || !sessionId || window.parent === window) return;
    const currentConversation = conversations.find((item) => item.session_id === sessionId);
    if (!currentConversation) return;
    window.parent.postMessage(
      {
        source: "aethercore-workbench",
        type: EMBED_NAVIGATION_EVENT,
        payload: {
          session_id: currentConversation.session_id,
          conversation_id: currentConversation.conversation_id,
          title: currentConversation.title,
        },
      },
      "*",
    );
  }, [conversations, isEmbedMode, sessionId]);

  const handleLoggedIn = async () => {
    const profile = await getCurrentUser();
    setCurrentUser(profile);
    setAuthed(true);
    setAuthModalOpen(false);

    if (pendingPath.startsWith("/workbench")) {
      await ensureWorkbenchSession();
    }

    navigate(pendingPath || "/workbench", { replace: true });
  };

  const requireAuth = (targetPath: string) => {
    const nextPath = targetPath || "/workbench";
    if (authed) {
      navigate(nextPath);
      return;
    }
    setPendingPath(nextPath);
    setAuthModalOpen(true);
  };

  const handleNewConversation = () => {
    setSessionId("");
    setIsNewSession(true);
    updateWorkbenchQuery("", true);
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
    updateWorkbenchQuery(newSessionId, false);
    void refreshConversations(newSessionId);
  };

  const handleLogout = () => {
    clearAccessToken();
    setAuthed(false);
    setSessionId("");
    setConversations([]);
    setCurrentUser(null);
    setIsEmbedMode(false);
    setIsNewSession(false);
    setShowPersonalSettingsDialog(false);
    navigate("/", { replace: true });
  };

  const pageFallback = (
    <main className="app-loading">
      <p>正在加载界面...</p>
    </main>
  );

  if (!ready) {
    return (
      <main className="app-loading">
        <p>正在初始化 AetherCore...</p>
      </main>
    );
  }

  const shellClassName = isEmbedMode || location.pathname.startsWith("/workbench") ? "workspace-shell workspace-shell--workbench" : "workspace-shell";

  return (
    <div className={shellClassName}>
      {!isEmbedMode && !location.pathname.startsWith("/workbench") ? (
        <AppChrome
          currentUser={currentUser}
          authed={authed}
          onRequireAuth={requireAuth}
          onOpenPersonalSettings={() => setShowPersonalSettingsDialog(true)}
          onLogout={handleLogout}
        />
      ) : null}

      <section className="workspace-shell__main">
        <Suspense fallback={pageFallback}>
          <Routes>
            <Route
              path="/"
              element={<HomePage authed={authed} onOpenChat={() => requireAuth("/workbench")} onOpenPlatforms={() => requireAuth("/platforms")} />}
            />
            <Route path="/chat" element={<Navigate to="/workbench" replace />} />
            <Route
              path="/workbench"
              element={
                authed ? (
                  sessionId || isNewSession ? (
                    <WorkbenchPage
                      conversations={conversations}
                      currentUser={currentUser}
                      isEmbedMode={isEmbedMode}
                      sessionId={sessionId}
                      isNewSession={isNewSession}
                      adminEntryHref={currentUser?.can_manage_platforms ? "/platforms" : undefined}
                      onLogout={handleLogout}
                      onNewConversation={handleNewConversation}
                      onDeleteSession={(id) => void handleDeleteSession(id)}
                      onRenameSession={(id, title) => void handleRenameSession(id, title)}
                      onSessionCreated={handleCreateSessionAndSelect}
                      onSessionRefresh={(id) => void refreshConversations(id || sessionId)}
                      onSessionSelect={(id) => {
                        setSessionId(id);
                        setIsNewSession(false);
                        updateWorkbenchQuery(id, false);
                      }}
                    />
                  ) : (
                    pageFallback
                  )
                ) : (
                  <Navigate to="/" replace />
                )
              }
            />
            <Route
              path="/platforms"
              element={authed && currentUser?.can_manage_platforms ? <PlatformsPage currentUser={currentUser} /> : authed ? <Navigate to="/workbench" replace /> : <Navigate to="/" replace />}
            />
            <Route
              path="/platforms/:platformId"
              element={authed && currentUser ? <PlatformDetailPage currentUser={currentUser} /> : <Navigate to="/" replace />}
            />
            <Route
              path="/system"
              element={authed && currentUser?.can_manage_system ? <AdminPage currentUser={currentUser} scope="system" /> : authed ? <Navigate to="/platforms" replace /> : <Navigate to="/" replace />}
            />
            <Route path="/admin" element={<Navigate to="/platforms" replace />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
      </section>

      <PersonalSettingsDialog
        currentUser={currentUser}
        open={showPersonalSettingsDialog}
        onClose={() => setShowPersonalSettingsDialog(false)}
        onLogout={handleLogout}
      />

      <AuthModal open={authModalOpen} onClose={() => setAuthModalOpen(false)} onLoggedIn={() => void handleLoggedIn()} />
    </div>
  );
}
