import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type RuntimeStatus = {
  version: string;
  profile: string;
  workspace_root: string;
  session: { id: string; name: string; message_count: number };
  context: { used: number; limit: number; percent: number };
  modes: { authorization: string; plan: boolean; thinking: boolean };
};

type RuntimeEvent = { kind: string; payload: unknown; timestamp: number };

const token = new URLSearchParams(window.location.search).get("token") || "";

async function api(path: string, init: RequestInit = {}) {
  const response = await fetch(path + (path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(token), {
    ...init,
    headers: { "content-type": "application/json", ...(init.headers || {}) }
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

function App() {
  const [status, setStatus] = useState<RuntimeStatus | null>(null);
  const [events, setEvents] = useState<RuntimeEvent[]>([]);
  const [message, setMessage] = useState("");
  const [activeTab, setActiveTab] = useState("chat");

  useEffect(() => {
    api("/api/status").then(setStatus).catch(console.error);
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocol}//${window.location.host}/api/events?token=${encodeURIComponent(token)}`);
    socket.onmessage = event => {
      const parsed = JSON.parse(event.data);
      setEvents(previous => [...previous.slice(-300), parsed]);
      if (parsed.kind === "status") setStatus(parsed.payload);
    };
    return () => socket.close();
  }, []);

  const content = useMemo(() => {
    if (activeTab === "chat") return <Chat events={events} message={message} setMessage={setMessage} />;
    if (activeTab === "workspace") return <Workspace />;
    if (activeTab === "settings") return <Settings />;
    if (activeTab === "sessions") return <Sessions />;
    return <Doctor />;
  }, [activeTab, events, message]);

  async function send() {
    if (!message.trim()) return;
    await api("/api/chat", { method: "POST", body: JSON.stringify({ message }) });
    setMessage("");
  }

  return (
    <main className="app">
      <aside className="sidebar">
        <h1>KAIRO</h1>
        <p>{status?.version || "0.3.0-preview"}</p>
        {["chat", "workspace", "settings", "sessions", "doctor"].map(tab => (
          <button className={tab === activeTab ? "active" : ""} onClick={() => setActiveTab(tab)} key={tab}>{tab}</button>
        ))}
      </aside>
      <section className="main">
        <header>
          <div>
            <strong>{status?.profile || "No profile"}</strong>
            <span>{status?.workspace_root || ""}</span>
          </div>
          <div className="context">
            <span>{Math.round(status?.context.percent || 0)}%</span>
            <progress value={status?.context.used || 0} max={status?.context.limit || 1} />
          </div>
        </header>
        {content}
        {activeTab === "chat" && (
          <footer>
            <textarea value={message} onChange={event => setMessage(event.target.value)} placeholder="Ask Kairo..." />
            <button onClick={send}>Send</button>
            <button onClick={() => api("/api/chat/stop", { method: "POST" })}>Stop</button>
          </footer>
        )}
      </section>
    </main>
  );
}

function Chat({ events }: { events: RuntimeEvent[]; message: string; setMessage: (value: string) => void }) {
  return <div className="panel stream">{events.map((event, index) => <pre key={index}>{event.kind}: {JSON.stringify(event.payload)}</pre>)}</div>;
}

function Workspace() {
  const [snapshot, setSnapshot] = useState<any>(null);
  useEffect(() => { api("/api/workspace/snapshot").then(setSnapshot).catch(console.error); }, []);
  return <div className="grid"><div className="panel"><h2>Files</h2>{snapshot?.files?.slice(0, 200).map((f: string) => <p key={f}>{f}</p>)}</div><div className="panel"><h2>Diff</h2><pre>{snapshot?.diff}</pre></div></div>;
}

function Settings() {
  const [config, setConfig] = useState<any>(null);
  useEffect(() => { api("/api/config").then(setConfig).catch(console.error); }, []);
  return <div className="panel"><h2>Settings</h2><pre>{JSON.stringify(config, null, 2)}</pre></div>;
}

function Sessions() {
  const [sessions, setSessions] = useState<any>(null);
  useEffect(() => { api("/api/sessions").then(setSessions).catch(console.error); }, []);
  return <div className="panel"><h2>Sessions</h2>{sessions?.sessions?.map((s: any) => <p key={s.id}>{s.name} - {s.message_count} messages</p>)}</div>;
}

function Doctor() {
  const [doctor, setDoctor] = useState<any>(null);
  return <div className="panel"><button onClick={() => api("/api/doctor", { method: "POST", body: JSON.stringify({ local_only: true }) }).then(setDoctor)}>Run Doctor</button><pre>{JSON.stringify(doctor, null, 2)}</pre></div>;
}

createRoot(document.getElementById("root")!).render(<App />);
