import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BookmarkPlus,
  ChevronDown,
  ChevronRight,
  File,
  Folder,
  FolderOpen,
  GitBranch,
  RefreshCw,
  Search,
  Star,
  Trash2
} from "lucide-react";
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
import type { WorkspaceChange, WorkspaceMoveResult, WorkspaceSnapshot } from "../types";

export type WorkspaceTreeNode = {
  name: string;
  path: string;
  kind: "directory" | "file";
  children: WorkspaceTreeNode[];
};

export function buildWorkspaceTree(paths: string[]): WorkspaceTreeNode[] {
  const root: WorkspaceTreeNode = { name: "", path: "", kind: "directory", children: [] };
  for (const rawPath of [...new Set(paths)].sort((a, b) => a.localeCompare(b))) {
    const parts = rawPath.split("/").filter(Boolean);
    let parent = root;
    parts.forEach((name, index) => {
      const path = parts.slice(0, index + 1).join("/");
      const kind = index === parts.length - 1 ? "file" : "directory";
      let node = parent.children.find(item => item.name === name);
      if (!node) {
        node = { name, path, kind, children: [] };
        parent.children.push(node);
      } else if (kind === "directory") {
        node.kind = "directory";
      }
      parent = node;
    });
  }
  const sort = (nodes: WorkspaceTreeNode[]) => {
    nodes.sort((a, b) => {
      if (a.kind !== b.kind) return a.kind === "directory" ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
    nodes.forEach(node => sort(node.children));
  };
  sort(root.children);
  return root.children;
}

export function filterWorkspaceTree(nodes: WorkspaceTreeNode[], query: string): WorkspaceTreeNode[] {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return nodes;
  return nodes.flatMap(node => {
    const children = filterWorkspaceTree(node.children, normalized);
    if (node.path.toLowerCase().includes(normalized) || children.length) {
      return [{ ...node, children }];
    }
    return [];
  });
}

export function WorkspacePage() {
  const [selected, setSelected] = useState("");
  const [target, setTarget] = useState("");
  const [lastFailedTarget, setLastFailedTarget] = useState("");
  const [moveError, setMoveError] = useState("");
  const [bookmarkName, setBookmarkName] = useState("");
  const [filter, setFilter] = useState("");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const client = useQueryClient();
  const pushToast = useRuntimeStore(state => state.pushToast);
  const status = useRuntimeStore(state => state.status);
  const identity = useRuntimeStore(state => state.workspace);
  const acceptWorkspace = useRuntimeStore(state => state.acceptWorkspace);
  const setStatus = useRuntimeStore(state => state.setStatus);
  const busy = Boolean(status?.task.busy);
  const degraded = status?.diagnostics?.degraded_reason || status?.lifecycle?.degraded_reason || "";

  const snapshot = useQuery({
    queryKey: ["workspace", "snapshot", identity.runtimeId, identity.revision, selected],
    queryFn: () => getWorkspaceSnapshot(selected)
  });
  const bookmarks = useQuery({ queryKey: ["workspace-bookmarks"], queryFn: getWorkspaceBookmarks });
  const activeFile = selected || snapshot.data?.selected_file || snapshot.data?.active_file || "";
  const preview = useQuery({
    queryKey: ["workspace-file", identity.runtimeId, identity.revision, activeFile],
    queryFn: () => getWorkspaceFile(activeFile),
    enabled: Boolean(activeFile)
  });

  const move = useMutation({
    mutationFn: moveWorkspace,
    onMutate: async nextTarget => {
      setMoveError("");
      setLastFailedTarget(nextTarget);
      await Promise.all([
        client.cancelQueries({ queryKey: ["workspace"] }),
        client.cancelQueries({ queryKey: ["workspace-file"] })
      ]);
    },
    onSuccess: async result => {
      applyWorkspaceResult(result);
      setTarget("");
      setFilter("");
      setCollapsed({});
      setMoveError("");
      await Promise.all([
        client.removeQueries({ queryKey: ["workspace-file"] }),
        client.invalidateQueries({ queryKey: ["workspace"] }),
        client.invalidateQueries({ queryKey: ["settings"] }),
        client.invalidateQueries({ queryKey: ["sessions"] }),
        client.invalidateQueries({ queryKey: ["skills"] })
      ]);
      pushToast({ tone: "success", text: result.message || "Workspace switch committed." });
    },
    onError: error => {
      const text = String((error as Error).message || error);
      setMoveError(text);
      pushToast({ tone: "error", text });
    }
  });

  function applyWorkspaceResult(result: WorkspaceMoveResult) {
    const nextSnapshot = result.snapshot;
    const nextStatus = result.status;
    const root = result.workspace_root || result.root || nextSnapshot?.root || nextStatus?.workspace_root || "";
    const runtimeId = result.runtime_id || nextSnapshot?.runtime_id || nextStatus?.runtime_id || "";
    const revision = Number(result.workspace_revision ?? nextSnapshot?.workspace_revision ?? nextStatus?.workspace_revision ?? identity.revision);
    if (!acceptWorkspace({ runtimeId, revision, root })) return;
    if (nextStatus) setStatus(nextStatus);

    const selectedStillExists = Boolean(selected && nextSnapshot?.files.includes(selected));
    const backendSelection = nextSnapshot?.selected_file || nextSnapshot?.active_file || "";
    const nextSelected = selectedStillExists ? selected : (backendSelection && nextSnapshot?.files.includes(backendSelection) ? backendSelection : "");
    setSelected(nextSelected);
    if (nextSnapshot) {
      client.setQueryData(
        ["workspace", "snapshot", runtimeId || identity.runtimeId, revision, nextSelected],
        nextSnapshot
      );
    }
  }

  const saveBookmark = useMutation({
    mutationFn: () => addWorkspaceBookmark(bookmarkName, snapshot.data?.root || identity.root || target || "."),
    onSuccess: async () => {
      setBookmarkName("");
      await client.invalidateQueries({ queryKey: ["workspace-bookmarks"] });
      pushToast({ tone: "success", text: "Bookmark saved." });
    },
    onError: error => pushToast({ tone: "error", text: String((error as Error).message || error) })
  });
  const removeBookmark = useMutation({
    mutationFn: removeWorkspaceBookmark,
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ["workspace-bookmarks"] });
      pushToast({ tone: "success", text: "Bookmark removed." });
    },
    onError: error => pushToast({ tone: "error", text: String((error as Error).message || error) })
  });

  const rawTree = useMemo(() => buildWorkspaceTree(snapshot.data?.files || []), [snapshot.data?.files]);
  const tree = useMemo(() => filterWorkspaceTree(rawTree, filter), [filter, rawTree]);
  const changes = snapshot.data?.changes || [];
  const groupedChanges = useMemo(() => groupChanges(changes), [changes]);
  const mutationBlocked = busy || Boolean(degraded) || move.isPending;
  const rootMismatch = Boolean(
    snapshot.data?.root
    && status?.workspace_root
    && snapshot.data.root !== status.workspace_root
  );

  return (
    <div className="page workspace-page">
      <header className="page-header">
        <div>
          <span className="section-kicker">Workspace</span>
          <h2>{snapshot.data?.root || identity.root || "Project files"}</h2>
          <small className="workspace-revision">
            {identity.runtimeId ? `runtime ${identity.runtimeId.slice(0, 8)} · revision ${identity.revision}` : "Waiting for runtime identity"}
          </small>
        </div>
        <div className="toolbar">
          {snapshot.data?.tree_truncated ? <Badge tone="warn">tree truncated</Badge> : null}
          {snapshot.data?.file_limit ? <Badge>{snapshot.data.file_count || snapshot.data.files.length}/{snapshot.data.file_limit} files</Badge> : null}
          <button
            className="secondary-button"
            onClick={() => client.invalidateQueries({ queryKey: ["workspace"] })}
            disabled={snapshot.isFetching}
          >
            <RefreshCw className={snapshot.isFetching ? "spin" : ""} size={16} /> Refresh
          </button>
        </div>
      </header>

      {busy ? <div className="warning-box">A task is running. Finish or stop it before switching workspace or editing bookmarks.</div> : null}
      {degraded ? <div className="error-banner">Runtime is read-only because recovery failed: {degraded}</div> : null}
      {rootMismatch ? <div className="warning-box">Refreshing workspace state: the file snapshot does not match the runtime root yet.</div> : null}
      {moveError ? (
        <div className="error-panel compact workspace-move-error" role="alert">
          <strong>Workspace switch failed</strong>
          <span>{moveError}</span>
          <button className="secondary-button" onClick={() => move.mutate(lastFailedTarget)} disabled={mutationBlocked || !lastFailedTarget}>Retry</button>
        </div>
      ) : null}

      <section className="workspace-controls">
        <Field label="Switch workspace" hint="The runtime commits config, shell, tools and file monitor together.">
          <div className="inline-form">
            <input
              value={target}
              onChange={event => setTarget(event.target.value)}
              placeholder="Absolute path or bookmark"
              disabled={mutationBlocked}
            />
            <button
              className="primary-button"
              onClick={() => target.trim() && move.mutate(target.trim())}
              disabled={mutationBlocked || !target.trim()}
            >
              <FolderOpen size={16} /> {move.isPending ? "Switching…" : "Switch"}
            </button>
          </div>
        </Field>
        <Field label="Save current root as bookmark">
          <div className="inline-form">
            <input value={bookmarkName} onChange={event => setBookmarkName(event.target.value)} placeholder="Bookmark name" disabled={mutationBlocked} />
            <button className="secondary-button" onClick={() => bookmarkName.trim() && saveBookmark.mutate()} disabled={mutationBlocked || !bookmarkName.trim()}>
              <BookmarkPlus size={16} /> Save
            </button>
          </div>
        </Field>
      </section>

      {snapshot.isLoading ? <EmptyState title="Loading workspace" detail="Reading the authoritative runtime snapshot…" /> : null}
      {snapshot.isError ? <div className="error-banner">Workspace snapshot failed: {String((snapshot.error as Error).message || snapshot.error)}</div> : null}

      <div className="workspace-grid">
        <section className="surface file-tree">
          <div className="surface-header">
            <strong>Files</strong>
            <Badge>{snapshot.data?.files.length || 0}</Badge>
          </div>
          {snapshot.data?.tree_truncated ? (
            <div className="warning-box compact">Showing the first {snapshot.data.file_limit} files from the backend snapshot.</div>
          ) : null}
          <div className="file-search">
            <Search size={15} />
            <input value={filter} onChange={event => setFilter(event.target.value)} placeholder="Search files" />
          </div>
          <div className="file-tree-scroll" role="tree">
            {tree.length ? tree.map(node => (
              <TreeNode
                key={node.path}
                node={node}
                depth={0}
                selected={activeFile}
                collapsed={collapsed}
                forceExpanded={Boolean(filter.trim())}
                onToggle={path => setCollapsed(current => ({ ...current, [path]: !current[path] }))}
                onSelect={setSelected}
              />
            )) : <EmptyState title={filter ? "No matching files" : "Workspace is empty"} />}
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
                <ChangeGroup title="Staged" items={groupedChanges.staged} activeFile={activeFile} onSelect={setSelected} />
                <ChangeGroup title="Unstaged" items={groupedChanges.unstaged} activeFile={activeFile} onSelect={setSelected} />
                <ChangeGroup title="Untracked" items={groupedChanges.untracked} activeFile={activeFile} onSelect={setSelected} />
                <ChangeGroup title="Deleted" items={groupedChanges.deleted} activeFile={activeFile} onSelect={setSelected} />
                <ChangeGroup title="Renamed" items={groupedChanges.renamed} activeFile={activeFile} onSelect={setSelected} />
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
                <button onClick={() => move.mutate(bookmark.path || bookmark.name)} disabled={mutationBlocked}>
                  <GitBranch size={14} />
                  <span>{bookmark.name}</span>
                </button>
                <button className="icon-button" onClick={() => removeBookmark.mutate(bookmark.name)} disabled={mutationBlocked}>
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
            <pre className="code-block diff-block code-viewer">{snapshot.data?.diff || "Select a changed file to review."}</pre>
            {activeFile ? (
              <pre className="code-block file-preview code-viewer">
                {preview.data?.binary
                  ? "Binary file; preview unavailable."
                  : preview.data?.content || safeJson(preview.error || "Loading preview…")}
              </pre>
            ) : null}
          </div>
        </section>
      </div>
    </div>
  );
}

function TreeNode({
  node,
  depth,
  selected,
  collapsed,
  forceExpanded,
  onToggle,
  onSelect
}: {
  node: WorkspaceTreeNode;
  depth: number;
  selected: string;
  collapsed: Record<string, boolean>;
  forceExpanded: boolean;
  onToggle: (path: string) => void;
  onSelect: (path: string) => void;
}) {
  const isDirectory = node.kind === "directory";
  const isCollapsed = !forceExpanded && Boolean(collapsed[node.path]);
  return (
    <>
      <button
        className={node.path === selected ? "tree-row active" : "tree-row"}
        style={{ paddingLeft: `${10 + Math.min(depth, 10) * 14}px` }}
        onClick={() => isDirectory ? onToggle(node.path) : onSelect(node.path)}
        role="treeitem"
        aria-expanded={isDirectory ? !isCollapsed : undefined}
      >
        {isDirectory ? (
          isCollapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />
        ) : <span className="tree-indent" />}
        {isDirectory ? <Folder size={15} /> : <File size={14} />}
        <span title={node.path}>{node.name}</span>
      </button>
      {isDirectory && !isCollapsed ? node.children.map(child => (
        <TreeNode
          key={child.path}
          node={child}
          depth={depth + 1}
          selected={selected}
          collapsed={collapsed}
          forceExpanded={forceExpanded}
          onToggle={onToggle}
          onSelect={onSelect}
        />
      )) : null}
    </>
  );
}

function groupChanges(changes: WorkspaceChange[]) {
  return {
    session: changes.filter(change => change.session_touched),
    staged: changes.filter(change => change.staged && !change.session_touched),
    unstaged: changes.filter(change => !change.staged && !change.untracked && !change.session_touched && !change.status.includes("D") && !change.status.includes("R") && /[MA]/.test(change.status)),
    untracked: changes.filter(change => change.untracked && !change.session_touched),
    deleted: changes.filter(change => change.status.includes("D") && !change.session_touched),
    renamed: changes.filter(change => change.status.includes("R") && !change.session_touched),
    other: changes.filter(change => !change.staged && !change.untracked && !change.session_touched && !change.status.includes("D") && !change.status.includes("R") && !/[MA]/.test(change.status))
  };
}

function ChangeGroup({
  title,
  items,
  activeFile,
  onSelect
}: {
  title: string;
  items: WorkspaceChange[];
  activeFile: string;
  onSelect: (path: string) => void;
}) {
  if (!items.length) return null;
  return (
    <div className="change-group">
      <span className="section-kicker">{title}</span>
      {items.map(change => (
        <button className={change.path === activeFile ? "change-row active" : "change-row"} key={change.path} onClick={() => onSelect(change.path)}>
          <Badge tone={changeTone(change)}>{change.status}</Badge>
          <span>{change.path}</span>
        </button>
      ))}
    </div>
  );
}

function changeTone(change: WorkspaceChange): "neutral" | "good" | "warn" | "bad" | "info" {
  if (change.session_touched) return "info";
  if (change.untracked) return "warn";
  if (change.status.includes("D")) return "bad";
  if (change.staged) return "good";
  return "neutral";
}
