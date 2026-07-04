import React, { useEffect, useMemo, useState } from "react";
import { QueryClient, QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import { Activity, Bot, FolderGit2, HeartPulse, MessageSquare, Settings, Sparkles } from "lucide-react";
import { eventUrl, getChatHistory, getStatus } from "./api";
import { Badge, Meter, Toasts, formatNumber } from "./components";
import { useRuntimeStore } from "./stores";
import type { RuntimeEvent, RuntimeStatus } from "./types";
import { ChatPage } from "./pages/ChatPage";
import { WorkspacePage } from "./pages/WorkspacePage";
import { SessionsPage } from "./pages/SessionsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { DoctorPage } from "./pages/DoctorPage";

type Page = "chat" | "workspace" | "sessions" | "settings" | "doctor";

const queryClient = new QueryClient();

const navItems: Array<{ id: Page; label: string; icon: React.ComponentType<{ size?: number }> }> = [
  { id: "chat", label: "Chat", icon: MessageSquare },
  { id: "workspace", label: "Workspace", icon: FolderGit2 },
  { id: "sessions", label: "Sessions", icon: Bot },
  { id: "settings", label: "Settings", icon: Settings },
  { id: "doctor", label: "Doctor", icon: HeartPulse }
];

export function AppRoot() {
  return (
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  );
}

function App() {
  const [page, setPage] = useState<Page>("chat");
  const status = useRuntimeStore(state => state.status);
  const setStatus = useRuntimeStore(state => state.setStatus);
  const applyEvent = useRuntimeStore(state => state.applyEvent);
  const setHistory = useRuntimeStore(state => state.setHistory);
  const pushToast = useRuntimeStore(state => state.pushToast);
  const kaiState = useRuntimeStore(state => state.kaiState);
  const client = useQueryClient();

  useEffect(() => {
    getStatus().then(setStatus).catch(error => pushToast({ tone: "error", text: String(error.message || error) }));
    getChatHistory().then(history => setHistory(history.messages)).catch(() => undefined);
  }, [pushToast, setHistory, setStatus]);

  useEffect(() => {
    const socket = new WebSocket(eventUrl());
    socket.onmessage = event => {
      const parsed = JSON.parse(event.data) as RuntimeEvent;
      applyEvent(parsed);
      if (parsed.kind === "status") setStatus(parsed.payload as RuntimeStatus);
      if (["config_updated", "workspace_updated", "session_changed", "skills_updated", "usage_updated", "turn_finished"].includes(parsed.kind)) {
        client.invalidateQueries();
        getStatus().then(setStatus).catch(() => undefined);
      }
    };
    socket.onopen = () => pushToast({ tone: "success", text: "Connected to Kairo runtime." });
    socket.onerror = () => pushToast({ tone: "error", text: "WebSocket connection failed." });
    socket.onclose = () => pushToast({ tone: "info", text: "Runtime event stream closed." });
    return () => socket.close();
  }, [applyEvent, client, pushToast, setStatus]);

  const pageNode = useMemo(() => {
    if (page === "workspace") return <WorkspacePage />;
    if (page === "sessions") return <SessionsPage />;
    if (page === "settings") return <SettingsPage />;
    if (page === "doctor") return <DoctorPage />;
    return <ChatPage />;
  }, [page]);

  return (
    <main className="app-shell">
      <aside className="nav-rail">
        <div className="brand-mark" aria-label="Kairo">
          <span className="kai-glyph">✦</span>
          <div>
            <strong>KAIRO</strong>
            <small>{status?.version || "0.3.1-preview"}</small>
          </div>
        </div>
        <nav>
          {navItems.map(item => {
            const Icon = item.icon;
            return (
              <button key={item.id} className={page === item.id ? "active" : ""} onClick={() => setPage(item.id)}>
                <Icon size={18} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>
      </aside>

      <section className="workspace-shell">
        <header className="topbar">
          <div className="project-title">
            <span className="section-kicker">Project</span>
            <strong>{status?.workspace_root || "No workspace"}</strong>
          </div>
          <div className="topbar-cluster">
            <Badge tone={status?.task.busy ? "warn" : "good"}>{status?.task.busy ? "Running" : "Idle"}</Badge>
            <Badge tone="info">{status?.profile || "No profile"}</Badge>
            <Badge>{status?.modes.authorization || "manual"}</Badge>
          </div>
        </header>

        <div className="content-frame">
          <section className="primary-pane">{pageNode}</section>
          <aside className="inspector">
            <div className="kai-card">
              <div className={`kai-orb kai-${kaiState}`}>
                <Sparkles size={26} />
              </div>
              <div>
                <span className="section-kicker">Kai state</span>
                <strong>{kaiState}</strong>
              </div>
            </div>
            <Meter value={status?.context.used || 0} max={status?.context.limit || 1} label="Context" />
            <div className="inspector-block">
              <span className="section-kicker">Session</span>
              <strong>{status?.session.name || "Conversation"}</strong>
              <p>{formatNumber(status?.session.message_count)} messages</p>
            </div>
            <div className="inspector-block">
              <span className="section-kicker">Task</span>
              <strong>{status?.task.status || "Idle"}</strong>
              <p>{status?.task.current || "No active task"}</p>
            </div>
            <div className="inspector-block compact-grid">
              <span><Activity size={14} /> Plan {status?.modes.plan ? "ON" : "OFF"}</span>
              <span><Activity size={14} /> Think {status?.modes.thinking ? "ON" : "OFF"}</span>
              <span><Activity size={14} /> Token {formatNumber(status?.context.used)}</span>
            </div>
          </aside>
        </div>
      </section>
      <Toasts />
    </main>
  );
}
