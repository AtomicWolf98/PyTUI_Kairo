import React, { useEffect, useMemo, useRef, useState } from "react";
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
  Sparkles,
  WifiOff
} from "lucide-react";
import { createSession, eventUrl, getChatHistory, getSessions, getSettings, getStatus, getWorkspaceSnapshot, switchSession } from "./api";
import { Badge, Meter, Toasts, formatNumber } from "./components";
import { useRuntimeStore } from "./stores";
import type { RuntimeEvent, RuntimeStatus, SettingsViewModel } from "./types";
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
  const settings = useQuery({ queryKey: ["settings"], queryFn: getSettings });
  const status = useRuntimeStore(state => state.status);
  const setStatus = useRuntimeStore(state => state.setStatus);
  const applyEvent = useRuntimeStore(state => state.applyEvent);
  const setHistory = useRuntimeStore(state => state.setHistory);
  const pushToast = useRuntimeStore(state => state.pushToast);
  const kaiState = useRuntimeStore(state => state.kaiState);
  const connection = useRuntimeStore(state => state.connection);
  const setConnection = useRuntimeStore(state => state.setConnection);
  const client = useQueryClient();
  const connectedOnce = useRef(false);

  useEffect(() => {
    getStatus().then(setStatus).catch(error => pushToast({ tone: "error", text: String(error.message || error) }));
    getChatHistory().then(history => setHistory(history.messages)).catch(() => undefined);
  }, [pushToast, setHistory, setStatus]);

  useEffect(() => {
    applyAppearance(settings.data?.appearance);
  }, [settings.data?.appearance]);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let retryTimer: number | undefined;
    let disposed = false;
    let attempts = 0;

    const connect = () => {
      if (disposed) return;
      setConnection(attempts ? "reconnecting" : "connecting");
      socket = new WebSocket(eventUrl());
      socket.onmessage = event => {
        const parsed = JSON.parse(event.data) as RuntimeEvent;
        applyEvent(parsed);
        if (parsed.kind === "status") setStatus(parsed.payload as RuntimeStatus);
        void refreshForRuntimeEvent(client, parsed.kind, setStatus, setHistory);
      };
      socket.onopen = () => {
        attempts = 0;
        setConnection("connected");
        getStatus().then(setStatus).catch(() => undefined);
        if (!connectedOnce.current) {
          connectedOnce.current = true;
          pushToast({ tone: "success", text: "Connected to Kairo runtime." });
        }
      };
      socket.onerror = () => socket?.close();
      socket.onclose = () => {
        if (disposed) return;
        attempts += 1;
        setConnection("reconnecting");
        const delay = Math.min(10_000, 500 * (2 ** Math.min(attempts, 5)));
        retryTimer = window.setTimeout(connect, delay);
      };
    };

    connect();
    return () => {
      disposed = true;
      if (retryTimer !== undefined) window.clearTimeout(retryTimer);
      socket?.close();
      setConnection("disconnected");
    };
  }, [applyEvent, client, pushToast, setConnection, setHistory, setStatus]);

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
        {connection !== "connected" ? (
          <div className="connection-banner" role="status">
            <WifiOff size={15} />
            {connection === "reconnecting" ? "Runtime connection lost. Reconnecting…" : "Connecting to local runtime…"}
          </div>
        ) : null}
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

      <aside className={page === "workspace" ? "right-inspector workspace-context-inspector" : "right-inspector"}>
        {page === "workspace" ? <WorkspaceSummaryInspector /> : <WorkspaceInspector />}
        <RuntimeInspector status={status} kaiState={kaiState} />
      </aside>
      <Toasts />
    </main>
  );
}

async function refreshForRuntimeEvent(
  client: ReturnType<typeof useQueryClient>,
  kind: string,
  setStatus: (status: RuntimeStatus) => void,
  setHistory: (messages: Array<Record<string, unknown>>) => void
) {
  if (kind === "workspace_changed" || kind === "workspace_updated") {
    await Promise.all([
      client.invalidateQueries({ queryKey: ["workspace"] }),
      client.invalidateQueries({ queryKey: ["workspace-file"] }),
      client.invalidateQueries({ queryKey: ["settings"] }),
      client.invalidateQueries({ queryKey: ["skills"] })
    ]);
  } else if (kind === "session_changed") {
    await client.invalidateQueries({ queryKey: ["sessions"] });
    const history = await getChatHistory().catch(() => null);
    if (history) setHistory(history.messages);
  } else if (kind === "config_updated") {
    await Promise.all([
      client.invalidateQueries({ queryKey: ["settings"] }),
      client.invalidateQueries({ queryKey: ["workspace"] })
    ]);
  } else if (kind === "skills_updated") {
    await Promise.all([
      client.invalidateQueries({ queryKey: ["skills"] }),
      client.invalidateQueries({ queryKey: ["settings"] })
    ]);
  } else if (kind === "turn_finished") {
    await Promise.all([
      client.invalidateQueries({ queryKey: ["sessions"] }),
      client.invalidateQueries({ queryKey: ["workspace"] })
    ]);
    const history = await getChatHistory().catch(() => null);
    if (history) setHistory(history.messages);
  } else if (kind !== "usage_updated") {
    return;
  }
  const nextStatus = await getStatus().catch(() => null);
  if (nextStatus) setStatus(nextStatus);
}

function applyAppearance(appearance?: SettingsViewModel["appearance"]) {
  if (!appearance || typeof document === "undefined") return;
  const root = document.documentElement;
  const prefersLight = typeof window !== "undefined"
    && typeof window.matchMedia === "function"
    && window.matchMedia("(prefers-color-scheme: light)").matches;
  const theme = appearance.theme === "system"
    ? (prefersLight ? "light" : "dark")
    : appearance.theme === "kairo-light" ? "light" : "dark";
  const density = ["compact", "comfortable", "spacious"].includes(appearance.density)
    ? appearance.density
    : "comfortable";
  const fontSize = Math.min(22, Math.max(12, Number(appearance.font_size) || 14));
  root.dataset.kairoTheme = theme;
  root.dataset.kairoDensity = density;
  root.dataset.kairoAnimation = appearance.animation || "full";
  root.dataset.kairoMotion = appearance.reduced_motion ? "reduced" : "normal";
  root.dataset.kairoMascot = appearance.mascot ? "on" : "off";
  root.style.setProperty("--kairo-font-size", `${fontSize}px`);
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
  const busy = useRuntimeStore(state => Boolean(state.status?.task.busy));
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
      await Promise.all([
        client.invalidateQueries({ queryKey: ["sessions"] }),
        client.invalidateQueries({ queryKey: ["workspace"] })
      ]);
      const history = await getChatHistory();
      useRuntimeStore.getState().setHistory(history.messages);
    } catch (error) {
      pushToast({ tone: "error", text: String((error as Error).message || error) });
    }
  }
  return (
    <div className="rail-section">
      <div className="rail-header">
        <span>Conversations</span>
        <button className="icon-button" onClick={create} title={busy ? "Finish the running task first" : "New session"} disabled={busy}><Plus size={15} /></button>
      </div>
      <button className="new-chat-button" onClick={create} disabled={busy}><Plus size={15} /> New chat</button>
      <div className="rail-session-list">
        {(sessions.data?.sessions || []).slice(0, 12).map(session => (
          <button className={session.id === activeId ? "rail-session active" : "rail-session"} key={session.id} onClick={() => open(session.id)} disabled={busy}>
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
  const identity = useRuntimeStore(state => state.workspace);
  const snapshot = useQuery({
    queryKey: ["workspace", "inspector", identity.runtimeId, identity.revision],
    queryFn: () => getWorkspaceSnapshot("")
  });
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

function WorkspaceSummaryInspector() {
  const identity = useRuntimeStore(state => state.workspace);
  const snapshot = useQuery({
    queryKey: ["workspace", "inspector", "summary", identity.runtimeId, identity.revision],
    queryFn: () => getWorkspaceSnapshot("")
  });
  const files = snapshot.data?.files || [];
  const changes = snapshot.data?.changes || [];
  const touched = changes.filter(change => change.session_touched).length;
  const root = snapshot.data?.root || "Workspace";
  const parts = root.split(/[\\/]/).filter(Boolean);
  return (
    <section className="inspector-panel workspace-summary-panel">
      <div className="surface-header">
        <div>
          <span className="section-kicker">Workspace</span>
          <strong>{parts[parts.length - 1] || root}</strong>
        </div>
        <FolderGit2 size={18} />
      </div>
      <div className="workspace-summary-metrics">
        <div>
          <span>Files</span>
          <strong>{formatNumber(files.length)}</strong>
        </div>
        <div>
          <span>Changes</span>
          <strong>{formatNumber(changes.length)}</strong>
        </div>
        <div>
          <span>Touched</span>
          <strong>{formatNumber(touched)}</strong>
        </div>
      </div>
      <p className="inspector-note">File tree, changed files, bookmarks and diff review now live in the Workspace tab.</p>
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
