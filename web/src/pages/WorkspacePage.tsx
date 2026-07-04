import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookmarkPlus, FolderOpen, GitBranch, RefreshCw, Star, Trash2 } from "lucide-react";
import {
  addWorkspaceBookmark,
  getWorkspaceBookmarks,
  getWorkspaceFile,
  getWorkspaceSnapshot,
  moveWorkspace,
  removeWorkspaceBookmark
} from "../api";
import { Badge, EmptyState, Field, safeJson } from "../components";
import { useRuntimeStore } from "../stores";

export function WorkspacePage() {
  const [selected, setSelected] = useState("");
  const [target, setTarget] = useState("");
  const [bookmarkName, setBookmarkName] = useState("");
  const client = useQueryClient();
  const pushToast = useRuntimeStore(state => state.pushToast);
  const snapshot = useQuery({ queryKey: ["workspace", selected], queryFn: () => getWorkspaceSnapshot(selected) });
  const bookmarks = useQuery({ queryKey: ["workspace-bookmarks"], queryFn: getWorkspaceBookmarks });
  const preview = useQuery({
    queryKey: ["workspace-file", selected],
    queryFn: () => getWorkspaceFile(selected),
    enabled: Boolean(selected)
  });

  const move = useMutation({
    mutationFn: moveWorkspace,
    onSuccess: result => {
      pushToast({ tone: result.ok ? "success" : "error", text: result.message || "Workspace updated." });
      client.invalidateQueries();
    },
    onError: error => pushToast({ tone: "error", text: String((error as Error).message || error) })
  });

  const saveBookmark = useMutation({
    mutationFn: () => addWorkspaceBookmark(bookmarkName, snapshot.data?.root || target || "."),
    onSuccess: () => {
      setBookmarkName("");
      client.invalidateQueries({ queryKey: ["workspace-bookmarks"] });
      pushToast({ tone: "success", text: "Bookmark saved." });
    },
    onError: error => pushToast({ tone: "error", text: String((error as Error).message || error) })
  });

  const tree = useMemo(() => snapshot.data?.files.slice(0, 600) || [], [snapshot.data?.files]);
  const changes = snapshot.data?.changes || [];
  const activeFile = selected || snapshot.data?.selected_file || "";

  return (
    <div className="page workspace-page">
      <header className="page-header">
        <div>
          <span className="section-kicker">Workspace</span>
          <h2>{snapshot.data?.root || "Project files"}</h2>
        </div>
        <div className="toolbar">
          {snapshot.data?.tree_truncated ? <Badge tone="warn">tree truncated</Badge> : null}
          <button className="secondary-button" onClick={() => client.invalidateQueries({ queryKey: ["workspace"] })}>
            <RefreshCw size={16} /> Refresh
          </button>
        </div>
      </header>

      <section className="workspace-controls">
        <Field label="Move workspace">
          <div className="inline-form">
            <input value={target} onChange={event => setTarget(event.target.value)} placeholder="Path or bookmark" />
            <button className="primary-button" onClick={() => target.trim() && move.mutate(target.trim())}>
              <FolderOpen size={16} /> Move
            </button>
          </div>
        </Field>
        <Field label="Save current root as bookmark">
          <div className="inline-form">
            <input value={bookmarkName} onChange={event => setBookmarkName(event.target.value)} placeholder="Bookmark name" />
            <button className="secondary-button" onClick={() => bookmarkName.trim() && saveBookmark.mutate()}>
              <BookmarkPlus size={16} /> Save
            </button>
          </div>
        </Field>
      </section>

      <div className="workspace-grid">
        <section className="surface file-tree">
          <div className="surface-header">
            <strong>Files</strong>
            <Badge>{tree.length}</Badge>
          </div>
          {tree.length ? tree.map(path => (
            <button className={path === activeFile ? "tree-row active" : "tree-row"} key={path} onClick={() => setSelected(path)}>
              <span style={{ paddingLeft: `${Math.min(path.split("/").length - 1, 6) * 10}px` }}>{path}</span>
            </button>
          )) : <EmptyState title="No files found" />}
        </section>

        <section className="surface changes-list">
          <div className="surface-header">
            <strong>Changes</strong>
            <Badge tone={changes.length ? "warn" : "good"}>{changes.length}</Badge>
          </div>
          {changes.length ? changes.map(change => (
            <button className={change.path === activeFile ? "change-row active" : "change-row"} key={change.path} onClick={() => setSelected(change.path)}>
              <Badge tone={change.session_touched ? "info" : change.untracked ? "warn" : "neutral"}>{change.status}</Badge>
              <span>{change.path}</span>
            </button>
          )) : <EmptyState title="No working tree changes" detail="Session-touched files and Git changes will appear here." />}

          <div className="bookmarks-block">
            <div className="surface-header">
              <strong>Bookmarks</strong>
              <Star size={15} />
            </div>
            {(bookmarks.data?.bookmarks || []).map(bookmark => (
              <div className="bookmark-row" key={bookmark.name}>
                <button onClick={() => move.mutate(bookmark.name)}>
                  <GitBranch size={14} />
                  <span>{bookmark.name}</span>
                </button>
                <button className="icon-button" onClick={() => {
                  removeWorkspaceBookmark(bookmark.name)
                    .then(() => client.invalidateQueries({ queryKey: ["workspace-bookmarks"] }))
                    .catch(error => pushToast({ tone: "error", text: String((error as Error).message || error) }));
                }}>
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
        </section>

        <section className="surface diff-viewer">
          <div className="surface-header">
            <strong>{activeFile || "Review"}</strong>
            {snapshot.data?.diff_truncated ? <Badge tone="warn">truncated</Badge> : null}
          </div>
          {snapshot.data?.error ? <div className="error-banner">{snapshot.data.error}</div> : null}
          <div className="split-preview">
            <pre className="code-block diff-block">{snapshot.data?.diff || "Select a changed file to review."}</pre>
            {selected ? (
              <pre className="code-block file-preview">
                {preview.data?.binary ? "Binary file; preview unavailable." : preview.data?.content || safeJson(preview.error || "Loading preview...")}
              </pre>
            ) : null}
          </div>
        </section>
      </div>
    </div>
  );
}
