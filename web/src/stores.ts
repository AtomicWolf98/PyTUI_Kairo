import { create } from "zustand";
import type { ChatMessageView, RuntimeEvent, RuntimeStatus, ToolApproval, ToolRunView } from "./types";

type Toast = { id: string; tone: "info" | "success" | "error"; text: string };

type RuntimeState = {
  status: RuntimeStatus | null;
  messages: ChatMessageView[];
  approvals: ToolApproval[];
  kaiState: string;
  toasts: Toast[];
  setStatus: (status: RuntimeStatus) => void;
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

function normalizeHistory(messages: Array<Record<string, unknown>>): ChatMessageView[] {
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

function appendToActiveMessage(messages: ChatMessageView[], field: "content" | "thought", delta: string) {
  const next = [...messages];
  let index = findLastMessageIndex(next, message => Boolean(message.streaming) && (message.role === "assistant" || message.role === "plan"));
  if (index < 0) {
    next.push({ id: nowId("assistant"), role: "assistant", content: "", streaming: true });
    index = next.length - 1;
  }
  const current = next[index];
  next[index] = { ...current, [field]: `${current[field] || ""}${delta}` };
  return next;
}

function updateActiveTool(messages: ChatMessageView[], update: ToolRunView): ChatMessageView[] {
  const next = [...messages];
  let index = findLastMessageIndex(next, message => message.role === "assistant" || message.role === "plan");
  if (index < 0) {
    next.push({ id: nowId("assistant"), role: "assistant", content: "", tools: [] });
    index = next.length - 1;
  }
  const message = next[index];
  const tools = [...(message.tools || [])];
  const existing = tools.findIndex(tool => tool.id === update.id || tool.name === update.name);
  if (existing >= 0) {
    tools[existing] = { ...tools[existing], ...update };
  } else {
    tools.push(update);
  }
  next[index] = { ...message, tools };
  return next;
}

export const useRuntimeStore = create<RuntimeState>((set, get) => ({
  status: null,
  messages: [],
  approvals: [],
  kaiState: "idle",
  toasts: [],
  setStatus: status => set({ status }),
  setHistory: messages => set({ messages: normalizeHistory(messages) }),
  pushToast: toast => {
    const item = { ...toast, id: nowId("toast") };
    set({ toasts: [...get().toasts.slice(-4), item] });
  },
  dismissToast: id => set({ toasts: get().toasts.filter(toast => toast.id !== id) }),
  applyEvent: event => {
    const payload = payloadObject(event.payload);
    if (event.kind === "status") {
      set({ status: event.payload as RuntimeStatus });
      return;
    }
    if (event.kind === "state") {
      set({ kaiState: asString(event.payload) || "idle" });
      return;
    }
    if (event.kind === "turn_started") {
      const text = asString(payload.text);
      set({ messages: [...get().messages, { id: asString(payload.turn_id) || nowId("user"), role: "user", content: text }] });
      return;
    }
    if (event.kind === "message_started") {
      const kind = asString(payload.kind) === "plan" ? "plan" : "assistant";
      set({ messages: [...get().messages, { id: nowId(kind), role: kind, content: "", thought: "", streaming: true }] });
      return;
    }
    if (event.kind === "content_delta") {
      set({ messages: appendToActiveMessage(get().messages, "content", asString(event.payload)) });
      return;
    }
    if (event.kind === "thought_delta") {
      set({ messages: appendToActiveMessage(get().messages, "thought", asString(event.payload)) });
      return;
    }
    if (event.kind === "message_finished") {
      set({ messages: get().messages.map(message => message.streaming ? { ...message, streaming: false } : message) });
      return;
    }
    if (event.kind === "tool_requested" || event.kind === "tool_started" || event.kind === "tool_finished") {
      const status = event.kind === "tool_started" ? "running" : event.kind === "tool_finished"
        ? (payload.success === false ? "failed" : "finished")
        : "requested";
      set({
        messages: updateActiveTool(get().messages, {
          id: asString(payload.id) || `${asString(payload.name)}-${asString(payload.target_path)}`,
          name: asString(payload.name) || "tool",
          arguments: asString(payload.arguments),
          target_path: asString(payload.target_path) || undefined,
          status,
          result: asString(payload.result) || undefined,
          success: typeof payload.success === "boolean" ? payload.success : undefined
        })
      });
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
    if (event.kind === "notice" || event.kind === "error") {
      const tone = event.kind === "error" ? "error" : "info";
      const text = asString(event.payload);
      set({
        messages: [...get().messages, { id: nowId(event.kind), role: event.kind, content: text }],
        toasts: [...get().toasts.slice(-4), { id: nowId("toast"), tone, text }]
      });
    }
  }
}));
