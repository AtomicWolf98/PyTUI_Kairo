import React, { useEffect, useRef, useState } from "react";
import { FolderGit2, PauseCircle, Play, SendHorizontal, ShieldCheck, Sparkles, Wrench } from "lucide-react";
import { approveTool, sendChat, stopChat } from "../api";
import { Badge, EmptyState } from "../components";
import { useRuntimeStore } from "../stores";
import type { ChatMessageView, ToolApproval } from "../types";

export function ChatPage() {
  const [draft, setDraft] = useState("");
  const messages = useRuntimeStore(state => state.messages);
  const approvals = useRuntimeStore(state => state.approvals);
  const status = useRuntimeStore(state => state.status);
  const pushToast = useRuntimeStore(state => state.pushToast);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages.length, messages[messages.length - 1]?.content]);

  async function submit() {
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    try {
      await sendChat(text);
    } catch (error) {
      pushToast({ tone: "error", text: String((error as Error).message || error) });
      setDraft(text);
    }
  }

  async function stop() {
    try {
      const result = await stopChat();
      pushToast({ tone: result.ok ? "info" : "error", text: result.message || "Stop requested." });
    } catch (error) {
      pushToast({ tone: "error", text: String((error as Error).message || error) });
    }
  }

  return (
    <div className="page chat-page">
      <header className="page-header">
        <div>
          <span className="section-kicker">Conversation</span>
          <h2>{status?.session.name || "Chat"}</h2>
        </div>
        <div className="toolbar">
          <Badge tone={status?.task.busy ? "warn" : "good"}>{status?.task.busy ? "streaming" : "ready"}</Badge>
          <button className="secondary-button" onClick={stop}>
            <PauseCircle size={16} /> Stop
          </button>
        </div>
      </header>

      <div className="chat-stream" ref={scrollRef}>
        {messages.length === 0 ? (
          <WelcomeBoard />
        ) : messages.map(message => <MessageCard key={message.id} message={message} />)}
      </div>

      {approvals.length ? <ApprovalTray approvals={approvals} /> : null}

      <footer className="composer-panel">
        <textarea
          value={draft}
          onChange={event => setDraft(event.target.value)}
          onKeyDown={event => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void submit();
            }
          }}
          placeholder="Ask Kairo to build, inspect, explain, or operate on the workspace..."
          rows={Math.min(8, Math.max(3, draft.split("\n").length))}
        />
        <div className="composer-bottom">
          <div className="composer-modes">
            <Badge tone="info">{status?.profile || "No profile"}</Badge>
            <Badge>{status?.modes.authorization || "manual"}</Badge>
            <Badge tone={status?.modes.plan ? "warn" : "neutral"}>Plan {status?.modes.plan ? "ON" : "OFF"}</Badge>
            <Badge tone={status?.modes.thinking ? "warn" : "neutral"}>Think {status?.modes.thinking ? "ON" : "OFF"}</Badge>
          </div>
          <button className="primary-button" onClick={submit} disabled={!draft.trim() || Boolean(status?.task.busy)}>
            <SendHorizontal size={17} /> Send
          </button>
        </div>
      </footer>
    </div>
  );
}

function WelcomeBoard() {
  const status = useRuntimeStore(state => state.status);
  return (
    <section className="welcome-board">
      <div className="welcome-mark"><Sparkles size={30} /></div>
      <div>
        <span className="section-kicker">Kairo Workbench</span>
        <h2>Build anything in this project</h2>
        <p>{status?.workspace_root || "Select a workspace, configure a model, then start a conversation."}</p>
      </div>
      <div className="welcome-grid">
        <div><FolderGit2 size={17} /><strong>Inspect</strong><span>Read files, diffs and project structure.</span></div>
        <div><Wrench size={17} /><strong>Operate</strong><span>Run tools with approval and clear output.</span></div>
        <div><Sparkles size={17} /><strong>Configure</strong><span>Use Settings for providers, models and skills.</span></div>
      </div>
      <EmptyState title="Try a task" detail="Ask: review this project, add tests, explain this file, or implement a focused change." />
    </section>
  );
}

function MessageCard({ message }: { message: ChatMessageView }) {
  const isSystemLike = message.role === "notice" || message.role === "error";
  return (
    <article className={`message-card message-${message.role}`}>
      <div className="message-meta">
        <Badge tone={message.role === "error" ? "bad" : message.role === "user" ? "info" : "neutral"}>
          {message.role === "plan" ? "plan" : message.role}
        </Badge>
        {message.streaming ? <span className="stream-dot">streaming</span> : null}
      </div>
      {message.thought ? (
        <details className="thought-block">
          <summary>Thinking</summary>
          <pre>{message.thought}</pre>
        </details>
      ) : null}
      <div className={isSystemLike ? "notice-text" : "message-content"}>
        {message.content || (message.streaming ? "..." : "")}
      </div>
      {message.tools?.length ? (
        <div className="tool-stack">
          {message.tools.map(tool => (
            <div className={`tool-card tool-${tool.status}`} key={tool.id}>
              <div>
                <Wrench size={15} />
                <strong>{tool.name}</strong>
                <Badge tone={tool.status === "failed" ? "bad" : tool.status === "finished" ? "good" : "warn"}>{tool.status}</Badge>
              </div>
              {tool.target_path ? <small>{tool.target_path}</small> : null}
              {tool.arguments ? <pre>{tool.arguments}</pre> : null}
              {tool.result ? <pre>{tool.result}</pre> : null}
            </div>
          ))}
        </div>
      ) : null}
    </article>
  );
}

function ApprovalTray({ approvals }: { approvals: ToolApproval[] }) {
  const pushToast = useRuntimeStore(state => state.pushToast);

  async function choose(id: string, choice: number) {
    try {
      await approveTool(id, choice);
    } catch (error) {
      pushToast({ tone: "error", text: String((error as Error).message || error) });
    }
  }

  return (
    <section className="approval-tray">
      {approvals.map(approval => (
        <div className="approval-card" key={approval.id}>
          <div>
            <ShieldCheck size={18} />
            <strong>{approval.prompt}</strong>
          </div>
          <div className="approval-actions">
            {approval.options.map((option, index) => (
              <button className={index === approval.default_index ? "primary-button" : "secondary-button"} key={option} onClick={() => choose(approval.id, index)}>
                {index === approval.default_index ? <Play size={14} /> : null}
                {option}
              </button>
            ))}
          </div>
        </div>
      ))}
    </section>
  );
}
