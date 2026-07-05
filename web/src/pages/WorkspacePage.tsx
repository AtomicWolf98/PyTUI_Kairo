import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookmarkPlus, ChevronDown, ChevronRight, FolderOpen, GitBranch, RefreshCw, Search, Star, Trash2 } from "lucide-react";
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
  const [filter, setFilter] = useState("");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
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

  const tree = useMemo(() => {
    const files = snapshot.data?.files.slice(0, 900) || [];
    const q = filter.trim().toLowerCase();
    return files.filter(path => {
      if (q && !path.toLowerCase().includes(q)) return false;
      const parts = path.split("/");
      for (let index = 1; index < parts.length; index += 1) {
        const dir = parts.slice(0, index).join("/");
        if (collapsed[dir]) return false;
      }
      return true;
    });
  }, [collapsed, filter, snapshot.data?.files]);
  const changes = snapshot.data?.changes || [];
  const groupedChanges = useMemo(() => {
    return {
      session: changes.filter(change => change.session_touched),
      untracked: changes.filter(change => change.untracked && !change.session_touched),
      other: changes.filter(change => !change.untracked && !change.session_touched)
    };
  }, [changes]);
  const activeFile = selected || snapshot.data?.selected_file || "";
  const folders = useMemo(() => {
    const names = new Set<string>();
    for (const file of snapshot.data?.files || []) {
      const parts = file.split("/");
      for (let index = 1; index < parts.length; index += 1) {
        names.add(parts.slice(0, index).join("/"));
      }
    }
    return names;
  }, [snapshot.data?.files]);

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
          <div className="file-search">
            <Search size={15} />
            <input value={filter} onChange={event => setFilter(event.target.value)} placeholder="Search files" />
          </div>
          <div className="file-tree-scroll">
            {tree.length ? tree.map(path => {
              const parent = path.split("/").slice(0, -1).join("/");
              const isFolderLike = folders.has(path);
              const toggleTarget = isFolderLike ? path : parent;
              return (
                <button className={path === activeFile ? "tree-row active" : "tree-row"} key={path} onClick={() => setSelected(path)}>
                  <span style={{ paddingLeft: `${Math.min(path.split("/").length - 1, 6) * 10}px` }}>
                    {toggleTarget && folders.has(toggleTarget) ? (
                      <span
                        className="tree-toggle"
                        onClick={event => {
                          event.stopPropagation();
                          setCollapsed({ ...collapsed, [toggleTarget]: !collapsed[toggleTarget] });
                        }}
                      >
                        {collapsed[toggleTarget] ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
                      </span>
                    ) : null}
                    {path}
                  </span>
                </button>
              );
            }) : <EmptyState title="No files found" />}
          </div>
        </section>

        <section className="surface changes-list">
          <div className="surface-header">
            <strong>Changes</strong>
            <Badge tone={changes.length ? "warn" : "good"}>{changes.length}</Badge>
          </div>
          <div className="changes-scroll">
            {changes.length ? (
              <>
              <ChangeGroup title="Session touched" items={groupedChanges.session} activeFile={activeFile} onSelect={setSelected} />
              <ChangeGroup title="Untracked" items={groupedChanges.untracked} activeFile={activeFile} onSelect={setSelected} />
              <ChangeGroup title="Other changes" items={groupedChanges.other} activeFile={activeFile} onSelect={setSelected} />
              </>
            ) : <EmptyState title="No working tree changes" detail="Session-touched files and Git changes will appear here." />}
          </div>

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

function ChangeGroup({
  title,
  items,
  activeFile,
  onSelect
}: {
  title: string;
  items: Array<{ path: string; status: string; session_touched: boolean; untracked: boolean }>;
  activeFile: string;
  onSelect: (path: string) => void;
}) {
  if (!items.length) return null;
  return (
    <div className="change-group">
      <span className="section-kicker">{title}</span>
      {items.map(change => (
        <button className={change.path === activeFile ? "change-row active" : "change-row"} key={change.path} onClick={() => onSelect(change.path)}>
          <Badge tone={change.session_touched ? "info" : change.untracked ? "warn" : "neutral"}>{change.status}</Badge>
          <span>{change.path}</span>
        </button>
      ))}
    </div>
  );
}
