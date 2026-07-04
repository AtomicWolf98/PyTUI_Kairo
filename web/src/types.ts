export type RuntimeStatus = {
  version: string;
  model: string;
  profile: string;
  base_url: string;
  api_key: string;
  workspace_root: string;
  session: { id: string; name: string; message_count: number };
  context: { used: number; limit: number; percent: number };
  modes: { authorization: string; plan: boolean; thinking: boolean };
  task: { current: string; status: string; busy: boolean };
  web?: { token_required: boolean };
};

export type RuntimeEvent = {
  kind: string;
  payload: unknown;
  timestamp: number;
};

export type ChatMessageView = {
  id: string;
  role: "user" | "assistant" | "tool" | "plan" | "notice" | "error";
  content: string;
  thought?: string;
  name?: string;
  streaming?: boolean;
  status?: string;
  tools?: ToolRunView[];
};

export type ToolRunView = {
  id: string;
  name: string;
  arguments: string;
  target_path?: string;
  status: "requested" | "running" | "finished" | "failed";
  result?: string;
  success?: boolean;
};

export type ToolApproval = {
  id: string;
  prompt: string;
  options: string[];
  default_index: number;
};

export type WorkspaceChange = {
  path: string;
  status: string;
  session_touched: boolean;
  staged: boolean;
  untracked: boolean;
};

export type WorkspaceSnapshot = {
  root: string;
  files: string[];
  changes: WorkspaceChange[];
  session_touched: string[];
  active_file: string;
  selected_file: string;
  diff: string;
  diff_truncated: boolean;
  tree_truncated: boolean;
  error: string;
};

export type WorkspaceFilePreview = {
  ok: boolean;
  path: string;
  size: number;
  binary: boolean;
  truncated: boolean;
  language: string;
  content: string;
};

export type WorkspaceBookmark = { name: string; path: string };

export type SessionSummary = {
  id: string;
  name: string;
  message_count: number;
  created_at: string;
  updated_at: string;
  context_used: number;
};

export type SessionsResponse = {
  active_session_id: string;
  sessions: SessionSummary[];
};

export type ConfigProfile = {
  id: string;
  label?: string;
  provider: string;
  base_url: string;
  api_key?: string;
  api_key_source?: string;
  api_key_env?: string;
  model: string;
  temperature: number;
  max_tokens: number;
  context_window: number;
  context_management?: Record<string, unknown>;
};

export type ConfigViewModel = {
  llm: {
    active_profile?: string;
    defaults?: Record<string, unknown>;
    profiles?: ConfigProfile[];
  };
  profiles_summary?: Array<ConfigProfile & { api_key_source?: string }>;
  model_roles?: Record<string, string>;
  context_management?: Record<string, unknown>;
  sessions?: Record<string, unknown>;
  ui?: Record<string, unknown>;
  web?: Record<string, unknown>;
  policy?: Record<string, unknown>;
  workspace_root?: string;
  workspace_bookmarks?: WorkspaceBookmark[];
  skills_dir?: string;
  shell_type?: string;
  authorization_level?: string;
  plan_mode?: boolean;
  thinking_mode?: boolean;
  active?: RuntimeStatus;
};

export type ProviderSetting = {
  id: string;
  name: string;
  base_url: string;
  api_key: string;
  api_key_source: string;
  model_count: number;
  profiles: string[];
};

export type SettingsViewModel = {
  version: string;
  general: {
    language: string;
    shell_type: string;
    authorization_level: string;
    plan_mode: boolean;
    thinking_mode: boolean;
    open_browser: boolean;
    show_thinking: boolean;
    expand_tools: boolean;
  };
  providers: ProviderSetting[];
  profiles: ConfigProfile[];
  roles: Record<string, string>;
  assistant: {
    name: string;
    system_prompt: string;
    default_mode: string;
    authorization_level: string;
    plan_mode: boolean;
    thinking_mode: boolean;
    context_management: Record<string, unknown>;
  };
  user: {
    name: string;
    timezone: string;
    preferences: string;
    default_instruction: string;
  };
  workbench: {
    workspace_root: string;
    skills_dir: string;
    shell_type: string;
    workspace_bookmarks: WorkspaceBookmark[];
    workspace_max_files: number;
    workspace_diff_max_bytes: number;
    workspace_refresh_seconds: number;
  };
  appearance: {
    theme: string;
    tui_theme: string;
    density: string;
    font_size: number;
    animation: string;
    mascot: boolean;
    reduced_motion: boolean;
  };
  skills: {
    skills_dir: string;
    require_hash: boolean;
  };
  raw: ConfigViewModel;
};

export type SkillList = {
  tools: Array<{
    name: string;
    description: string;
    permission: string;
    source: string;
    parameters: Record<string, unknown>;
  }>;
};

export type DoctorResult = {
  ok: boolean;
  message: string;
  checks: Array<Record<string, unknown>>;
};
