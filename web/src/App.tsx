import React, { useEffect, useMemo, useState } from "react";
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  Bot,
  FolderGit2,
  HeartPulse,
  MessageSquare,
  Plus,
  Search,
  Settings,
  Sparkles
} from "lucide-react";
import { createSession, eventUrl, getChatHistory, getSessions, getStatus, getWorkspaceSnapshot, switchSession } from "./api";
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
    <main className="desktop-shell">
      <aside className="left-activity">
        <div className="brand-button" aria-label="Kairo">K</div>
        {navItems.map(item => {
          const Icon = item.icon;
          return (
            <button key={item.id} className={page === item.id ? "active" : ""} onClick={() => setPage(item.id)} title={item.label}>
              <Icon size={19} />
            </button>
          );
        })}
      </aside>

      <aside className="project-rail">
        <ProjectHeader status={status} />
        <SessionRail onOpenSessions={() => setPage("sessions")} />
      </aside>

      <section className="main-workbench">
        <header className="desktop-titlebar">
          <div className="search-pill"><Search size={15} /> Search Kairo <kbd>Ctrl+K</kbd></div>
          <div className="titlebar-cluster">
            <Badge tone={status?.task.busy ? "warn" : "good"}>{status?.task.busy ? "Running" : "Idle"}</Badge>
            <Badge tone="info">{status?.profile || "No profile"}</Badge>
            <Badge>{status?.modes.authorization || "manual"}</Badge>
          </div>
        </header>
        <div className="page-canvas">{pageNode}</div>
      </section>

      <aside className="right-inspector">
        <WorkspaceInspector />
        <RuntimeInspector status={status} kaiState={kaiState} />
      </aside>
      <Toasts />
    </main>
  );
}

function ProjectHeader({ status }: { status?: RuntimeStatus | null }) {
  const root = status?.workspace_root || "No workspace";
  const parts = root.split(/[\\/]/).filter(Boolean);
  return (
    <div className="project-header">
      <span className="section-kicker">Project</span>
      <strong>{parts[parts.length - 1] || root}</strong>
      <small>{root}</small>
    </div>
  );
}

function SessionRail({ onOpenSessions }: { onOpenSessions: () => void }) {
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: getSessions });
  const client = useQueryClient();
  const pushToast = useRuntimeStore(state => state.pushToast);
  const activeId = sessions.data?.active_session_id || "";
  async function create() {
    try {
      await createSession("New conversation");
      await client.invalidateQueries({ queryKey: ["sessions"] });
      pushToast({ tone: "success", text: "New session created." });
    } catch (error) {
      pushToast({ tone: "error", text: String((error as Error).message || error) });
    }
  }
  async function open(id: string) {
    try {
      await switchSession(id);
      await client.invalidateQueries();
    } catch (error) {
      pushToast({ tone: "error", text: String((error as Error).message || error) });
    }
  }
  return (
    <div className="rail-section">
      <div className="rail-header">
        <span>Conversations</span>
        <button className="icon-button" onClick={create} title="New session"><Plus size={15} /></button>
      </div>
      <button className="new-chat-button" onClick={create}><Plus size={15} /> New chat</button>
      <div className="rail-session-list">
        {(sessions.data?.sessions || []).slice(0, 12).map(session => (
          <button className={session.id === activeId ? "rail-session active" : "rail-session"} key={session.id} onClick={() => open(session.id)}>
            <strong>{session.name}</strong>
            <span>{formatNumber(session.message_count)} messages</span>
          </button>
        ))}
      </div>
      <button className="text-link" onClick={onOpenSessions}>Manage all sessions</button>
    </div>
  );
}

function WorkspaceInspector() {
  const snapshot = useQuery({ queryKey: ["workspace", "inspector"], queryFn: () => getWorkspaceSnapshot("") });
  const files = snapshot.data?.files || [];
  const changes = snapshot.data?.changes || [];
  return (
    <section className="inspector-panel file-panel">
      <div className="surface-header">
        <div>
          <span className="section-kicker">Workspace</span>
          <strong>{changes.length} changes</strong>
        </div>
        <FolderGit2 size={18} />
      </div>
      <div className="mini-file-tree">
        {files.slice(0, 18).map(file => <div className="mini-file-row" key={file}>{file}</div>)}
      </div>
      <div className="mini-change-list">
        {changes.slice(0, 8).map(change => (
          <div className="mini-change-row" key={change.path}>
            <span>{change.path}</span>
            <Badge tone={change.session_touched ? "info" : change.untracked ? "warn" : "neutral"}>{change.status}</Badge>
          </div>
        ))}
      </div>
    </section>
  );
}

function RuntimeInspector({ status, kaiState }: { status?: RuntimeStatus | null; kaiState: string }) {
  return (
    <section className="inspector-panel runtime-panel">
      <div className="kai-card">
        <div className={`kai-orb kai-${kaiState}`}>
          <Sparkles size={24} />
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
    </section>
  );
}
