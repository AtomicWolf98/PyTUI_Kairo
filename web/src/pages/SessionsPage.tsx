import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Pencil, Plus, Search, Trash2 } from "lucide-react";
import { createSession, deleteSession, exportSession, getChatHistory, getSessions, renameSession, searchSessions, switchSession } from "../api";
import { Badge, EmptyState, Field, formatNumber } from "../components";
import { useRuntimeStore } from "../stores";

export function SessionsPage() {
  const [newName, setNewName] = useState("");
  const [search, setSearch] = useState("");
  const [renaming, setRenaming] = useState<Record<string, string>>({});
  const client = useQueryClient();
  const setHistory = useRuntimeStore(state => state.setHistory);
  const pushToast = useRuntimeStore(state => state.pushToast);
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: getSessions });
  const searchResult = useQuery({ queryKey: ["session-search", search], queryFn: () => searchSessions(search), enabled: Boolean(search.trim()) });

  const create = useMutation({
    mutationFn: createSession,
    onSuccess: async () => {
      setNewName("");
      await refreshAll(client, setHistory);
      pushToast({ tone: "success", text: "Session created." });
    },
    onError: error => pushToast({ tone: "error", text: String((error as Error).message || error) })
  });

  const activeId = sessions.data?.active_session_id || "";
  const results = useMemo(() => new Set((searchResult.data?.results || []).map(item => item.id)), [searchResult.data?.results]);
  const list = sessions.data?.sessions || [];

  async function switchTo(id: string) {
    try {
      await switchSession(id);
      await refreshAll(client, setHistory);
    } catch (error) {
      pushToast({ tone: "error", text: String((error as Error).message || error) });
    }
  }

  async function rename(id: string) {
    const name = (renaming[id] || "").trim();
    if (!name) return;
    try {
      await renameSession(id, name);
      setRenaming({ ...renaming, [id]: "" });
      await client.invalidateQueries({ queryKey: ["sessions"] });
      pushToast({ tone: "success", text: "Session renamed." });
    } catch (error) {
      pushToast({ tone: "error", text: String((error as Error).message || error) });
    }
  }

  async function remove(id: string) {
    if (!window.confirm("Delete this session? This cannot be undone from the WebUI.")) return;
    try {
      await deleteSession(id);
      await refreshAll(client, setHistory);
      pushToast({ tone: "success", text: "Session deleted." });
    } catch (error) {
      pushToast({ tone: "error", text: String((error as Error).message || error) });
    }
  }

  async function exportOne(id: string, format: "markdown" | "json") {
    try {
      const result = await exportSession(id, format);
      pushToast({ tone: "success", text: `Exported to ${result.path}` });
    } catch (error) {
      pushToast({ tone: "error", text: String((error as Error).message || error) });
    }
  }

  return (
    <div className="page sessions-page">
      <header className="page-header">
        <div>
          <span className="section-kicker">Sessions</span>
          <h2>Conversation Library</h2>
        </div>
        <Badge tone="info">{list.length} sessions</Badge>
      </header>

      <section className="session-actions">
        <Field label="New session">
          <div className="inline-form">
            <input value={newName} onChange={event => setNewName(event.target.value)} placeholder="Conversation name" />
            <button className="primary-button" onClick={() => create.mutate(newName || undefined as unknown as string)}>
              <Plus size={16} /> New
            </button>
          </div>
        </Field>
        <Field label="Search">
          <div className="inline-form">
            <input value={search} onChange={event => setSearch(event.target.value)} placeholder="Keyword" />
            <Search size={16} />
          </div>
        </Field>
      </section>

      <section className="session-list">
        {list.length ? list.map(session => {
          const matched = !search.trim() || results.has(session.id);
          if (!matched) return null;
          return (
            <article className={session.id === activeId ? "session-card active" : "session-card"} key={session.id}>
              <div>
                <strong>{session.name}</strong>
                <p>{formatNumber(session.message_count)} messages · context {formatNumber(session.context_used)}</p>
              </div>
              <div className="session-card-actions">
                <button className="secondary-button" onClick={() => switchTo(session.id)}>Switch</button>
                <input
                  value={renaming[session.id] || ""}
                  onChange={event => setRenaming({ ...renaming, [session.id]: event.target.value })}
                  placeholder="Rename"
                />
                <button className="icon-button" onClick={() => rename(session.id)}><Pencil size={15} /></button>
                <button className="icon-button" onClick={() => exportOne(session.id, "markdown")}><Download size={15} /></button>
                <button className="icon-button danger" onClick={() => remove(session.id)}><Trash2 size={15} /></button>
              </div>
            </article>
          );
        }) : <EmptyState title="No sessions yet" />}
      </section>
    </div>
  );
}

async function refreshAll(client: ReturnType<typeof useQueryClient>, setHistory: (messages: Array<Record<string, unknown>>) => void) {
  await client.invalidateQueries({ queryKey: ["sessions"] });
  const history = await getChatHistory();
  setHistory(history.messages);
}
