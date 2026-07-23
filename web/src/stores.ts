import { create } from "zustand";
import type { ChatMessageView, RuntimeEvent, RuntimeStatus, ToolApproval, ToolRunView } from "./types";

type Toast = { id: string; tone: "info" | "success" | "warn" | "error"; text: string };
export type ConnectionState = "connecting" | "connected" | "reconnecting" | "disconnected";
export type WorkspaceIdentity = { runtimeId: string; revision: number; root: string };

type RuntimeState = {
  status: RuntimeStatus | null;
  messages: ChatMessageView[];
  approvals: ToolApproval[];
  kaiState: string;
  connection: ConnectionState;
  workspace: WorkspaceIdentity;
  toasts: Toast[];
  setStatus: (status: RuntimeStatus) => void;
  setConnection: (connection: ConnectionState) => void;
  acceptWorkspace: (value: Partial<WorkspaceIdentity>) => boolean;
  setHistory: (messages: Array<Record<string, unknown>>) => void;
  applyEvent: (event: RuntimeEvent) => void;
  pushToast: (toast: Omit<Toast, "id">) => void;
  dismissToast: (id: string) => void;
};

const nowId = (prefix: string) => `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;

function asString(value: unknown): string {
  return typeof value === "string" ? value : value == null ? "" : JSON.stringify(value);
}

function payloadObject(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

export function normalizeHistory(messages: Array<Record<string, unknown>>): ChatMessageView[] {
  return messages.map((message, index) => {
    const role = asString(message.role);
    return {
      id: asString(message.id) || `history-${index}`,
      role: role === "tool" || role === "user" || role === "assistant" ? role : "notice",
      content: asString(message.content),
      name: asString(message.name) || undefined,
      tools: Array.isArray(message.tool_calls)
        ? message.tool_calls.map((tool, toolIndex) => {
          const record = payloadObject(tool);
          const fn = payloadObject(record.function);
          return {
            id: asString(record.id) || `history-tool-${index}-${toolIndex}`,
            name: asString(fn.name) || "tool",
            arguments: asString(fn.arguments),
            status: "finished"
          } satisfies ToolRunView;
        })
        : undefined
    };
  });
}

function findLastMessageIndex(messages: ChatMessageView[], predicate: (message: ChatMessageView) => boolean): number {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (predicate(messages[index])) return index;
  }
  return -1;
}

function payloadText(payload: Record<string, unknown>, raw: unknown): string {
  if (typeof payload.delta === "string") return payload.delta;
  if (typeof payload.value === "string") return payload.value;
  return asString(raw);
}

function appendToMessage(messages: ChatMessageView[], field: "content" | "thought", delta: string, messageId = "") {
  const next = [...messages];
  let index = messageId ? next.findIndex(message => message.id === messageId) : -1;
  if (index < 0) {
    index = findLastMessageIndex(next, message => Boolean(message.streaming) && (message.role === "assistant" || message.role === "plan"));
  }
  if (index < 0) {
    next.push({ id: messageId || nowId("assistant"), role: "assistant", content: "", streaming: true });
    index = next.length - 1;
  }
  const current = next[index];
  next[index] = { ...current, [field]: `${current[field] || ""}${delta}` };
  return next;
}

function updateActiveTool(messages: ChatMessageView[], update: ToolRunView, messageId = ""): ChatMessageView[] {
  const next = [...messages];
  let index = messageId ? next.findIndex(message => message.id === messageId) : -1;
  if (index < 0) {
    index = findLastMessageIndex(next, message => message.role === "assistant" || message.role === "plan");
  }
  if (index < 0) {
    next.push({ id: nowId("assistant"), role: "assistant", content: "", tools: [] });
    index = next.length - 1;
  }
  const message = next[index];
  const tools = [...(message.tools || [])];
  const existing = tools.findIndex(tool => tool.id === update.id);
  if (existing >= 0) {
    tools[existing] = { ...tools[existing], ...update };
  } else {
    tools.push(update);
  }
  next[index] = { ...message, tools };
  return next;
}

export function reduceRuntimeMessages(messages: ChatMessageView[], event: RuntimeEvent): ChatMessageView[] {
  const payload = payloadObject(event.payload);
  if (event.kind === "turn_started") {
    const text = asString(payload.text);
    const id = asString(payload.turn_id) || nowId("user");
    return [...messages, { id, role: "user", content: text }];
  }
  if (event.kind === "message_started") {
    const kind = asString(payload.kind) === "plan" ? "plan" : "assistant";
    const id = asString(payload.message_id) || nowId(kind);
    return [...messages, { id, role: kind, content: "", thought: "", streaming: true }];
  }
  if (event.kind === "content_delta") {
    return appendToMessage(messages, "content", payloadText(payload, event.payload), asString(payload.message_id));
  }
  if (event.kind === "thought_delta") {
    return appendToMessage(messages, "thought", payloadText(payload, event.payload), asString(payload.message_id));
  }
  if (event.kind === "message_finished") {
    const messageId = asString(payload.message_id);
    return messages.map(message => (!messageId || message.id === messageId) && message.streaming ? { ...message, streaming: false } : message);
  }
  if (event.kind === "tool_requested" || event.kind === "tool_started" || event.kind === "tool_finished") {
    const status = event.kind === "tool_started" ? "running" : event.kind === "tool_finished"
      ? (payload.success === false ? "failed" : "finished")
      : "requested";
    const toolId = asString(payload.tool_call_id) || asString(payload.id) || `${asString(payload.name)}-${asString(payload.target_path)}-${asString(payload.sequence)}`;
    return updateActiveTool(messages, {
      id: toolId,
      name: asString(payload.name) || "tool",
      arguments: asString(payload.arguments),
      target_path: asString(payload.target_path) || undefined,
      status,
      result: asString(payload.result) || undefined,
      success: typeof payload.success === "boolean" ? payload.success : undefined
    }, asString(payload.message_id));
  }
  if (event.kind === "notice" || event.kind === "error") {
    return [...messages, {
      id: nowId(event.kind),
      role: event.kind,
      content: payloadText(payload, event.payload)
    }];
  }
  return messages;
}

export function shouldAcceptWorkspace(current: WorkspaceIdentity, incoming: Partial<WorkspaceIdentity>): boolean {
  const runtimeId = incoming.runtimeId || current.runtimeId;
  const revision = Number(incoming.revision ?? current.revision);
  if (current.runtimeId && runtimeId && runtimeId !== current.runtimeId) return true;
  return revision >= current.revision;
}

export const useRuntimeStore = create<RuntimeState>((set, get) => ({
  status: null,
  messages: [],
  approvals: [],
  kaiState: "idle",
  connection: "connecting",
  workspace: { runtimeId: "", revision: 0, root: "" },
  toasts: [],
  setStatus: status => {
    const incoming = {
      runtimeId: status.runtime_id || "",
      revision: Number(status.workspace_revision || 0),
      root: status.workspace_root || ""
    };
    const current = get().workspace;
    if (!shouldAcceptWorkspace(current, incoming)) return;
    set({
      status,
      workspace: {
        runtimeId: incoming.runtimeId || current.runtimeId,
        revision: incoming.revision,
        root: incoming.root || current.root
      }
    });
  },
  setConnection: connection => set({ connection }),
  acceptWorkspace: value => {
    const current = get().workspace;
    if (!shouldAcceptWorkspace(current, value)) return false;
    set({
      workspace: {
        runtimeId: value.runtimeId || current.runtimeId,
        revision: Number(value.revision ?? current.revision),
        root: value.root || current.root
      }
    });
    return true;
  },
  setHistory: messages => set({ messages: normalizeHistory(messages) }),
  pushToast: toast => {
    const item = { ...toast, id: nowId("toast") };
    set({ toasts: [...get().toasts.slice(-4), item] });
  },
  dismissToast: id => set({ toasts: get().toasts.filter(toast => toast.id !== id) }),
  applyEvent: event => {
    const payload = payloadObject(event.payload);
    if (event.kind === "status") {
      get().setStatus(event.payload as RuntimeStatus);
      return;
    }
    if (event.kind === "state") {
      set({ kaiState: asString(payload.value) || asString(event.payload) || "idle" });
      return;
    }
    if (event.kind === "tool_requested" || event.kind === "tool_started" || event.kind === "tool_finished") {
      set({ messages: reduceRuntimeMessages(get().messages, event) });
      return;
    }
    if (["turn_started", "message_started", "content_delta", "thought_delta", "message_finished"].includes(event.kind)) {
      set({ messages: reduceRuntimeMessages(get().messages, event) });
      return;
    }
    if (event.kind === "tool_approval_requested") {
      const approval: ToolApproval = {
        id: asString(payload.id),
        prompt: asString(payload.prompt),
        options: Array.isArray(payload.options) ? payload.options.map(asString) : [],
        default_index: Number(payload.default_index || 0)
      };
      set({ approvals: [...get().approvals, approval] });
      return;
    }
    if (event.kind === "tool_approval_resolved") {
      set({ approvals: get().approvals.filter(approval => approval.id !== asString(payload.id)) });
      return;
    }
    if (event.kind === "stop_requested") {
      const resolved = Array.isArray(payload.resolved_approvals) ? payload.resolved_approvals.map(asString) : [];
      set({
        approvals: get().approvals.filter(approval => !resolved.includes(approval.id)),
        kaiState: "stopped"
      });
      return;
    }
    if (event.kind === "notice" || event.kind === "error") {
      const tone = event.kind === "error" ? "error" : "info";
      const text = payloadText(payload, event.payload);
      set({
        messages: reduceRuntimeMessages(get().messages, event),
        toasts: [...get().toasts.slice(-4), { id: nowId("toast"), tone, text }]
      });
    }
  }
}));
