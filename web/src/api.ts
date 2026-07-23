import type {
  ConfigViewModel,
  DoctorResult,
  RuntimeStatus,
  SessionsResponse,
  SettingsViewModel,
  SkillList,
  WorkspaceBookmark,
  WorkspaceFilePreview,
  WorkspaceMoveResult,
  WorkspaceSnapshot
} from "./types";

const TOKEN_STORAGE_KEY = "kairo.local.token";

function bootstrapToken() {
  const params = new URLSearchParams(window.location.search);
  const urlToken = params.get("token") || "";
  if (urlToken) {
    window.sessionStorage.setItem(TOKEN_STORAGE_KEY, urlToken);
    params.delete("token");
    const query = params.toString();
    const nextUrl = `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`;
    window.history.replaceState({}, document.title, nextUrl);
    return urlToken;
  }
  return window.sessionStorage.getItem(TOKEN_STORAGE_KEY) || "";
}

export const token = bootstrapToken();

type JsonValue = Record<string, unknown> | unknown[] | string | number | boolean | null;

export class ApiError extends Error {
  status: number;
  code: string;
  retryable: boolean;

  constructor(message: string, status: number, code = "", retryable = false) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.retryable = retryable;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      "content-type": "application/json",
      "x-kairo-token": token,
      ...(init.headers || {})
    }
  });
  if (!response.ok) {
    let message = await response.text();
    let code = "";
    let retryable = false;
    try {
      const parsed = JSON.parse(message) as {
        detail?: string | { message?: string };
        error?: string;
        code?: string;
        retryable?: boolean;
      };
      const detail = typeof parsed.detail === "string" ? parsed.detail : parsed.detail?.message;
      message = detail || parsed.error || message;
      code = parsed.code || "";
      retryable = Boolean(parsed.retryable);
    } catch {
      // Keep the raw response text.
    }
    throw new ApiError(message || `HTTP ${response.status}`, response.status, code, retryable);
  }
  return response.json() as Promise<T>;
}

export function getStatus() {
  return request<RuntimeStatus>("/api/status");
}

export function getConfig() {
  return request<ConfigViewModel>("/api/config");
}

export function getSettings() {
  return request<SettingsViewModel>("/api/settings/view");
}

export function patchSettings(section: string, payload: JsonValue) {
  return request<{ ok: boolean; settings: SettingsViewModel }>(`/api/settings/${encodeURIComponent(section)}`, {
    method: "PATCH",
    body: JSON.stringify(payload)
  });
}

export function createProvider(payload: JsonValue) {
  return request<{ ok: boolean; settings: SettingsViewModel }>("/api/settings/provider", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function patchProvider(providerId: string, payload: JsonValue) {
  return request<{ ok: boolean; settings: SettingsViewModel }>(`/api/settings/provider/${encodeURIComponent(providerId)}`, {
    method: "PATCH",
    body: JSON.stringify(payload)
  });
}

export function deleteProvider(providerId: string) {
  return request<{ ok: boolean; settings: SettingsViewModel }>(`/api/settings/provider/${encodeURIComponent(providerId)}`, {
    method: "DELETE"
  });
}

export function testProvider(providerId: string) {
  return request<{ ok: boolean; status: string; message: string }>(`/api/settings/provider/${encodeURIComponent(providerId)}/test`, {
    method: "POST"
  });
}

export function createProfile(payload: JsonValue) {
  return request<{ ok: boolean; settings: SettingsViewModel }>("/api/settings/profile", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function patchProfile(profileId: string, payload: JsonValue) {
  return request<{ ok: boolean; settings: SettingsViewModel }>(`/api/settings/profile/${encodeURIComponent(profileId)}`, {
    method: "PATCH",
    body: JSON.stringify(payload)
  });
}

export function deleteProfile(profileId: string) {
  return request<{ ok: boolean; settings: SettingsViewModel }>(`/api/settings/profile/${encodeURIComponent(profileId)}`, {
    method: "DELETE"
  });
}

export function patchConfig(section: string, payload: JsonValue) {
  return request<{ ok: boolean; config?: ConfigViewModel }>(`/api/config/${encodeURIComponent(section)}`, {
    method: "PATCH",
    body: JSON.stringify(payload)
  });
}

export function switchProfile(profileId: string) {
  return request<{ ok: boolean; message: string }>(`/api/config/profile/${encodeURIComponent(profileId)}/switch`, {
    method: "POST"
  });
}

export function exportConfig(withKeys = false, confirm = "") {
  return request<{ ok: boolean; with_keys: boolean; config: ConfigViewModel }>("/api/config/export", {
    method: "POST",
    body: JSON.stringify({ with_keys: withKeys, confirm })
  });
}

export function importConfig(path: string) {
  return request<{ ok: boolean; config: ConfigViewModel }>("/api/config/import", {
    method: "POST",
    body: JSON.stringify({ path })
  });
}

export function getChatHistory() {
  return request<{ session: { id: string; name: string }; messages: Array<Record<string, unknown>> }>("/api/chat/history");
}

export function sendChat(message: string) {
  return request<{ ok: boolean; turn_id: string }>("/api/chat", {
    method: "POST",
    body: JSON.stringify({ message })
  });
}

export function stopChat() {
  return request<{ ok: boolean; message: string }>("/api/chat/stop", { method: "POST" });
}

export function approveTool(id: string, choice: number) {
  return request<{ ok: boolean }>("/api/tools/approval", {
    method: "POST",
    body: JSON.stringify({ id, choice })
  });
}

export function getWorkspaceSnapshot(selectedFile = "") {
  const suffix = selectedFile ? `?selected_file=${encodeURIComponent(selectedFile)}` : "";
  return request<WorkspaceSnapshot>(`/api/workspace/snapshot${suffix}`);
}

export function getWorkspaceFile(path: string) {
  return request<WorkspaceFilePreview>(`/api/workspace/file?path=${encodeURIComponent(path)}`);
}

export function moveWorkspace(target: string) {
  return request<WorkspaceMoveResult>("/api/workspace/move", {
    method: "POST",
    body: JSON.stringify({ target })
  });
}

export function getWorkspaceBookmarks() {
  return request<{ bookmarks: WorkspaceBookmark[] }>("/api/workspace/bookmarks");
}

export function addWorkspaceBookmark(name: string, path: string) {
  return request<{ ok: boolean; bookmarks: WorkspaceBookmark[] }>("/api/workspace/bookmarks", {
    method: "POST",
    body: JSON.stringify({ name, path })
  });
}

export function removeWorkspaceBookmark(name: string) {
  return request<{ ok: boolean; bookmarks: WorkspaceBookmark[] }>(`/api/workspace/bookmarks/${encodeURIComponent(name)}`, {
    method: "DELETE"
  });
}

export function getSessions() {
  return request<SessionsResponse>("/api/sessions");
}

export function createSession(name: string) {
  return request<{ ok: boolean; session: { id: string; name: string } }>("/api/sessions", {
    method: "POST",
    body: JSON.stringify({ name })
  });
}

export function switchSession(id: string) {
  return request<{ ok: boolean }>(`/api/sessions/${encodeURIComponent(id)}/switch`, { method: "POST" });
}

export function renameSession(id: string, name: string) {
  return request<{ ok: boolean }>(`/api/sessions/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify({ name })
  });
}

export function deleteSession(id: string) {
  return request<{ ok: boolean }>(`/api/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export function searchSessions(q: string) {
  return request<{ keyword: string; results: Array<{ id: string; name: string; index: number }> }>(
    `/api/sessions/search?q=${encodeURIComponent(q)}`
  );
}

export function exportSession(id: string, format: "markdown" | "json") {
  return request<{ ok: boolean; path: string }>(`/api/sessions/${encodeURIComponent(id)}/export`, {
    method: "POST",
    body: JSON.stringify({ format })
  });
}

export function getSkills() {
  return request<SkillList>("/api/skills");
}

export function reloadSkills() {
  return request<SkillList & { ok: boolean }>("/api/skills/reload", { method: "POST" });
}

export function trustSkills(manifestDigest: string) {
  return request<SkillList & { ok: boolean }>("/api/skills/trust", {
    method: "POST",
    body: JSON.stringify({ manifest_digest: manifestDigest, persist: true })
  });
}

export function revokeSkillsTrust() {
  return request<SkillList & { ok: boolean }>("/api/skills/trust", { method: "DELETE" });
}

export function runDoctor(localOnly = true) {
  return request<DoctorResult>("/api/doctor", {
    method: "POST",
    body: JSON.stringify({ local_only: localOnly })
  });
}

export function eventUrl() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/api/events?token=${encodeURIComponent(token)}`;
}
