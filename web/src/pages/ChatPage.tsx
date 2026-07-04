import React, { useEffect, useRef, useState } from "react";
import { PauseCircle, Play, SendHorizontal, ShieldCheck, Wrench } from "lucide-react";
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
          <EmptyState title="Start a working conversation" detail="Ask Kairo to inspect, edit, explain, plan, or run tools in the current workspace." />
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
        <button className="primary-button" onClick={submit} disabled={!draft.trim() || Boolean(status?.task.busy)}>
          <SendHorizontal size={17} /> Send
        </button>
      </footer>
    </div>
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
