import React, { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  Download,
  EyeOff,
  KeyRound,
  MonitorCog,
  Plus,
  RotateCw,
  Save,
  Settings2,
  Sparkles,
  Trash2,
  UserRound,
  Wrench
} from "lucide-react";
import {
  createProfile,
  createProvider,
  deleteProfile,
  deleteProvider,
  exportConfig,
  getSettings,
  getSkills,
  importConfig,
  patchProfile,
  patchProvider,
  patchSettings,
  reloadSkills,
  switchProfile,
  testProvider
} from "../api";
import {
  Badge,
  ConfirmDialog,
  EmptyState,
  Field,
  Modal,
  NumberField,
  PasswordField,
  SelectField,
  SwitchField,
  TextareaField,
  TextField,
  safeJson
} from "../components";
import { useRuntimeStore } from "../stores";
import type { ConfigProfile, ProviderSetting, SettingsViewModel, WorkspaceBookmark } from "../types";

type SettingsTab =
  | "general"
  | "providers"
  | "models"
  | "roles"
  | "assistant"
  | "me"
  | "workbench"
  | "skills"
  | "appearance"
  | "export";

const tabs: Array<{ id: SettingsTab; label: string; icon: React.ComponentType<{ size?: number }> }> = [
  { id: "general", label: "General", icon: Settings2 },
  { id: "providers", label: "Providers", icon: KeyRound },
  { id: "models", label: "Models", icon: Sparkles },
  { id: "roles", label: "Roles", icon: Bot },
  { id: "assistant", label: "Assistant", icon: Bot },
  { id: "me", label: "Me", icon: UserRound },
  { id: "workbench", label: "Workbench", icon: MonitorCog },
  { id: "skills", label: "Skills", icon: Wrench },
  { id: "appearance", label: "Appearance", icon: Sparkles },
  { id: "export", label: "Import / Export", icon: Download }
];

export function SettingsPage() {
  const [tab, setTab] = useState<SettingsTab>("general");
  const settings = useQuery({ queryKey: ["settings"], queryFn: getSettings });
  const pushToast = useRuntimeStore(state => state.pushToast);

  useEffect(() => {
    if (settings.isError) {
      pushToast({ tone: "error", text: `Settings failed to load: ${formatError(settings.error)}` });
    }
  }, [pushToast, settings.error, settings.isError]);

  const versionLabel = settings.data?.version || "0.3.2-preview";

  return (
    <div className="settings-stage">
      <section className="settings-dialog">
        <aside className="settings-sidebar">
          <div className="settings-sidebar-title">
            <span className="section-kicker">Kairo Desktop</span>
            <strong>Settings</strong>
            <small>{versionLabel}</small>
          </div>
          <nav>
            {tabs.map(item => {
              const Icon = item.icon;
              return (
                <button className={tab === item.id ? "active" : ""} key={item.id} onClick={() => setTab(item.id)}>
                  <Icon size={17} />
                  <span>{item.label}</span>
                </button>
              );
            })}
          </nav>
        </aside>
        <main className="settings-content">
          {settings.isLoading ? <SettingsLoading /> : null}
          {settings.isError ? <SettingsError error={settings.error} onRetry={() => settings.refetch()} /> : null}
          {settings.data ? <SettingsTabView tab={tab} settings={settings.data} /> : null}
        </main>
      </section>
    </div>
  );
}

function SettingsLoading() {
  return (
    <div className="settings-state">
      <EmptyState
        title="Loading settings"
        detail="Requesting /api/settings/view from the local Kairo runtime..."
      />
    </div>
  );
}

function SettingsError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const message = formatError(error);
  const oldBackend = message.includes("404") || message.toLowerCase().includes("not found");
  return (
    <div className="settings-state">
      <div className="error-panel">
        <span className="section-kicker">Settings request failed</span>
        <h2>Unable to load settings</h2>
        <p>{message}</p>
        <p>
          {oldBackend
            ? "This usually means the browser is connected to an older Kairo backend. Restart Kairo or reinstall the current dev build."
            : "Check the local server token, backend logs, and whether the current dev server is still running."}
        </p>
        <div className="toolbar">
          <button className="primary-button" onClick={onRetry}>Retry</button>
        </div>
      </div>
    </div>
  );
}

function SettingsTabView({ tab, settings }: { tab: SettingsTab; settings: SettingsViewModel }) {
  let panel: React.ReactNode;
  if (tab === "providers") panel = <ProvidersPanel settings={settings} />;
  else if (tab === "models") panel = <ModelsPanel settings={settings} />;
  else if (tab === "roles") panel = <RolesPanel settings={settings} />;
  else if (tab === "assistant") panel = <AssistantPanel settings={settings} />;
  else if (tab === "me") panel = <MePanel settings={settings} />;
  else if (tab === "workbench") panel = <WorkbenchPanel settings={settings} />;
  else if (tab === "skills") panel = <SkillsPanel settings={settings} />;
  else if (tab === "appearance") panel = <AppearancePanel settings={settings} />;
  else if (tab === "export") panel = <ImportExportPanel settings={settings} />;
  else panel = <GeneralPanel settings={settings} />;
  return (
    <>
      {settings.diagnostics && !settings.diagnostics.version_match ? (
        <div className="warning-box version-warning">
          Frontend/backend version mismatch: backend {settings.diagnostics.backend_version || "unknown"}, static build {settings.diagnostics.static_version || "unknown"}. Rebuild WebUI or restart Kairo.
        </div>
      ) : null}
      {panel}
    </>
  );
}

function formatError(error: unknown) {
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;
  try {
    return JSON.stringify(error);
  } catch {
    return "Unknown error";
  }
}

function useSaveSection(section: string) {
  const client = useQueryClient();
  const pushToast = useRuntimeStore(state => state.pushToast);
  return useMutation({
    mutationFn: (payload: Record<string, unknown>) => patchSettings(section, payload),
    onSuccess: () => {
      client.invalidateQueries();
      pushToast({ tone: "success", text: "Settings saved." });
    },
    onError: error => pushToast({ tone: "error", text: String((error as Error).message || error) })
  });
}

function GeneralPanel({ settings }: { settings: SettingsViewModel }) {
  const [draft, setDraft] = useState(settings.general);
  const save = useSaveSection("general");
  useEffect(() => setDraft(settings.general), [settings.general]);
  return (
    <Panel title="General" kicker="Desktop behavior" action={<SaveButton onClick={() => save.mutate(draft)} />}>
      <div className="form-grid">
        <SelectField label="Language" value={draft.language} onChange={language => setDraft({ ...draft, language })} options={[
          { value: "system", label: "System" },
          { value: "zh-CN", label: "Simplified Chinese" },
          { value: "en-US", label: "English" }
        ]} />
        <SelectField label="Shell" value={draft.shell_type} onChange={shell_type => setDraft({ ...draft, shell_type })} options={[
          { value: "powershell", label: "PowerShell" },
          { value: "cmd", label: "cmd.exe" },
          { value: "bash", label: "Bash" }
        ]} />
        <SelectField label="Authorization" value={draft.authorization_level} onChange={authorization_level => setDraft({ ...draft, authorization_level })} options={[
          { value: "manual", label: "Manual approvals" },
          { value: "auto", label: "Auto approve safe tools" },
          { value: "yolo", label: "YOLO" }
        ]} />
      </div>
      <div className="settings-card-grid">
        <SwitchField label="Plan mode" checked={draft.plan_mode} onChange={plan_mode => setDraft({ ...draft, plan_mode })} />
        <SwitchField label="Thinking mode" checked={draft.thinking_mode} onChange={thinking_mode => setDraft({ ...draft, thinking_mode })} />
        <SwitchField label="Open browser on launch" checked={draft.open_browser} onChange={open_browser => setDraft({ ...draft, open_browser })} />
        <SwitchField label="Show thinking summaries" checked={draft.show_thinking} onChange={show_thinking => setDraft({ ...draft, show_thinking })} />
        <SwitchField label="Expand tool output" checked={draft.expand_tools} onChange={expand_tools => setDraft({ ...draft, expand_tools })} />
      </div>
    </Panel>
  );
}

function ProvidersPanel({ settings }: { settings: SettingsViewModel }) {
  const [selected, setSelected] = useState(settings.providers[0]?.id || "");
  const [creating, setCreating] = useState(false);
  const provider = settings.providers.find(item => item.id === selected) || settings.providers[0];
  useEffect(() => {
    if (!selected && settings.providers[0]) setSelected(settings.providers[0].id);
  }, [selected, settings.providers]);
  return (
    <Panel title="Providers" kicker="Base URLs and local keys" action={<button className="primary-button" onClick={() => setCreating(true)}><Plus size={16} /> Add provider</button>}>
      <div className="split-editor">
        <div className="list-panel">
          {settings.providers.map(item => (
            <button className={item.id === provider?.id ? "list-row active" : "list-row"} key={item.id} onClick={() => setSelected(item.id)}>
              <strong>{item.name}</strong>
              <span>{item.model_count} models · {item.api_key_source}</span>
            </button>
          ))}
        </div>
        {provider ? <ProviderEditor provider={provider} /> : <EmptyState title="No providers configured" />}
      </div>
      {creating ? <ProviderModal onClose={() => setCreating(false)} /> : null}
    </Panel>
  );
}

function ProviderEditor({ provider }: { provider: ProviderSetting }) {
  const [draft, setDraft] = useState({ ...provider, api_key_input: "", api_key_env: "" });
  const [confirmDelete, setConfirmDelete] = useState(false);
  const client = useQueryClient();
  const pushToast = useRuntimeStore(state => state.pushToast);
  useEffect(() => setDraft({ ...provider, api_key_input: "", api_key_env: "" }), [provider]);

  async function save(clear_key = false) {
    try {
      await patchProvider(provider.id, {
        name: draft.name,
        base_url: draft.base_url,
        api_key: clear_key ? "" : draft.api_key_input,
        api_key_env: draft.api_key_env,
        clear_key
      });
      client.invalidateQueries();
      pushToast({ tone: "success", text: clear_key ? "Provider key cleared." : "Provider saved." });
    } catch (error) {
      pushToast({ tone: "error", text: String((error as Error).message || error) });
    }
  }

  async function test() {
    try {
      const result = await testProvider(provider.id);
      pushToast({ tone: result.ok ? "success" : "warn", text: result.message });
    } catch (error) {
      pushToast({ tone: "error", text: String((error as Error).message || error) });
    }
  }

  async function remove() {
    try {
      await deleteProvider(provider.id);
      client.invalidateQueries();
      pushToast({ tone: "success", text: "Provider removed." });
    } catch (error) {
      pushToast({ tone: "error", text: String((error as Error).message || error) });
    }
  }

  return (
    <div className="editor-panel">
      <div className="surface-header">
        <div>
          <strong>{provider.name}</strong>
          <p>{provider.profiles.join(", ")}</p>
        </div>
        <Badge tone={provider.api_key_source.includes("missing") ? "warn" : "good"}>{provider.api_key_source}</Badge>
      </div>
      <TextField label="Provider name" value={draft.name} onChange={name => setDraft({ ...draft, name })} />
      <TextField label="Base URL" value={draft.base_url} onChange={base_url => setDraft({ ...draft, base_url })} />
      <PasswordField label="New API key" value={draft.api_key_input} onChange={api_key_input => setDraft({ ...draft, api_key_input })} placeholder="Leave blank to keep existing key" hint={`Current key: ${provider.api_key || "missing"}`} />
      <TextField label="API key env" value={draft.api_key_env} onChange={api_key_env => setDraft({ ...draft, api_key_env })} placeholder="KAIRO_PROVIDER_API_KEY" />
      <div className="toolbar">
        <button className="primary-button" onClick={() => save()}><Save size={16} /> Save</button>
        <button className="secondary-button" onClick={test}>Test config</button>
        <button className="secondary-button danger" onClick={() => save(true)}>Clear key</button>
        <button className="icon-button danger" onClick={() => setConfirmDelete(true)}><Trash2 size={16} /></button>
      </div>
      {confirmDelete ? (
        <ConfirmDialog
          title="Delete provider?"
          detail="This removes every profile attached to this provider."
          confirmLabel="Delete provider"
          danger
          onClose={() => setConfirmDelete(false)}
          onConfirm={() => {
            setConfirmDelete(false);
            void remove();
          }}
        />
      ) : null}
    </div>
  );
}

function ProviderModal({ onClose }: { onClose: () => void }) {
  const [draft, setDraft] = useState({
    id: "",
    base_url: "",
    model: "",
    label: "",
    api_key: "",
    context_window: 128000,
    max_tokens: 4000,
    temperature: 0.2
  });
  const client = useQueryClient();
  const pushToast = useRuntimeStore(state => state.pushToast);
  async function save() {
    try {
      await createProvider(draft);
      client.invalidateQueries();
      pushToast({ tone: "success", text: "Provider created." });
      onClose();
    } catch (error) {
      pushToast({ tone: "error", text: String((error as Error).message || error) });
    }
  }
  return (
    <Modal title="Add provider" onClose={onClose}>
      <div className="form-grid">
        <TextField label="Provider id" value={draft.id} onChange={id => setDraft({ ...draft, id })} placeholder="openai" />
        <TextField label="Base URL" value={draft.base_url} onChange={base_url => setDraft({ ...draft, base_url })} placeholder="https://api.openai.com/v1" />
        <TextField label="Model" value={draft.model} onChange={model => setDraft({ ...draft, model })} placeholder="gpt-4.1" />
        <TextField label="Label" value={draft.label} onChange={label => setDraft({ ...draft, label })} />
        <PasswordField label="API key" value={draft.api_key} onChange={api_key => setDraft({ ...draft, api_key })} />
        <NumberField label="Context window" value={draft.context_window} onChange={context_window => setDraft({ ...draft, context_window })} />
        <NumberField label="Max tokens" value={draft.max_tokens} onChange={max_tokens => setDraft({ ...draft, max_tokens })} />
        <NumberField label="Temperature" value={draft.temperature} step={0.1} onChange={temperature => setDraft({ ...draft, temperature })} />
      </div>
      <div className="toolbar modal-actions">
        <button className="secondary-button" onClick={onClose}>Cancel</button>
        <button className="primary-button" onClick={save} disabled={!draft.id.trim() || !draft.model.trim()}>Create provider</button>
      </div>
    </Modal>
  );
}

function ModelsPanel({ settings }: { settings: SettingsViewModel }) {
  const [selected, setSelected] = useState(settings.profiles[0]?.id || "");
  const [creating, setCreating] = useState(false);
  const profile = settings.profiles.find(item => item.id === selected) || settings.profiles[0];
  useEffect(() => {
    if (!selected && settings.profiles[0]) setSelected(settings.profiles[0].id);
  }, [selected, settings.profiles]);
  return (
    <Panel title="Models" kicker="Profiles, budgets and model routes" action={<button className="primary-button" onClick={() => setCreating(true)}><Plus size={16} /> Add model</button>}>
      <div className="split-editor">
        <div className="list-panel">
          {settings.profiles.map(item => (
            <button className={item.id === profile?.id ? "list-row active" : "list-row"} key={item.id} onClick={() => setSelected(item.id)}>
              <strong>{item.label || item.id}</strong>
              <span>{item.provider} · {item.model}</span>
            </button>
          ))}
        </div>
        {profile ? <ModelProfileEditor profile={profile} active={profile.id === settings.raw.llm.active_profile} /> : <EmptyState title="No model profiles" />}
      </div>
      {creating ? <ProfileModal settings={settings} onClose={() => setCreating(false)} /> : null}
    </Panel>
  );
}

function ModelProfileEditor({ profile, active }: { profile: ConfigProfile; active: boolean }) {
  const [draft, setDraft] = useState({ ...profile, api_key: "" });
  const [confirmDelete, setConfirmDelete] = useState(false);
  const client = useQueryClient();
  const pushToast = useRuntimeStore(state => state.pushToast);
  useEffect(() => setDraft({ ...profile, api_key: "" }), [profile]);

  async function save() {
    try {
      await patchProfile(profile.id, draft);
      client.invalidateQueries();
      pushToast({ tone: "success", text: "Model profile saved." });
    } catch (error) {
      pushToast({ tone: "error", text: String((error as Error).message || error) });
    }
  }

  async function activate() {
    try {
      const result = await switchProfile(profile.id);
      client.invalidateQueries();
      pushToast({ tone: result.ok ? "success" : "error", text: result.message });
    } catch (error) {
      pushToast({ tone: "error", text: String((error as Error).message || error) });
    }
  }

  async function remove() {
    try {
      await deleteProfile(profile.id);
      client.invalidateQueries();
      pushToast({ tone: "success", text: "Profile deleted." });
    } catch (error) {
      pushToast({ tone: "error", text: String((error as Error).message || error) });
    }
  }

  return (
    <div className="editor-panel">
      <div className="surface-header">
        <div>
          <strong>{profile.label || profile.id}</strong>
          <p>{profile.base_url}</p>
        </div>
        <button className={active ? "secondary-button active" : "secondary-button"} onClick={activate}>{active ? "Active" : "Use"}</button>
      </div>
      <div className="form-grid">
        <TextField label="Label" value={draft.label || ""} onChange={label => setDraft({ ...draft, label })} />
        <TextField label="Provider" value={draft.provider} onChange={provider => setDraft({ ...draft, provider })} />
        <TextField label="Base URL" value={draft.base_url} onChange={base_url => setDraft({ ...draft, base_url })} />
        <TextField label="Model" value={draft.model} onChange={model => setDraft({ ...draft, model })} />
        <NumberField label="Temperature" value={draft.temperature} step={0.1} onChange={temperature => setDraft({ ...draft, temperature })} />
        <NumberField label="Max tokens" value={draft.max_tokens} onChange={max_tokens => setDraft({ ...draft, max_tokens })} />
        <NumberField label="Context window" value={draft.context_window} onChange={context_window => setDraft({ ...draft, context_window })} />
        <TextField label="API key env" value={draft.api_key_env || ""} onChange={api_key_env => setDraft({ ...draft, api_key_env })} />
        <PasswordField label="Profile API key" value={draft.api_key || ""} onChange={api_key => setDraft({ ...draft, api_key })} placeholder="Leave blank to keep existing key" />
      </div>
      <div className="toolbar">
        <button className="primary-button" onClick={save}><Save size={16} /> Save profile</button>
        <button className="icon-button danger" onClick={() => setConfirmDelete(true)}><Trash2 size={16} /></button>
      </div>
      {confirmDelete ? (
        <ConfirmDialog
          title="Delete profile?"
          detail="This removes the model profile and any role routes pointing at it."
          confirmLabel="Delete profile"
          danger
          onClose={() => setConfirmDelete(false)}
          onConfirm={() => {
            setConfirmDelete(false);
            void remove();
          }}
        />
      ) : null}
    </div>
  );
}

function ProfileModal({ settings, onClose }: { settings: SettingsViewModel; onClose: () => void }) {
  const provider = settings.providers[0]?.id || "";
  const [draft, setDraft] = useState({
    id: provider ? `${provider}/new-model` : "",
    label: "",
    provider,
    base_url: settings.providers[0]?.base_url || "",
    model: "",
    api_key: "",
    api_key_env: "",
    temperature: 0.2,
    max_tokens: 4000,
    context_window: 128000
  });
  const client = useQueryClient();
  const pushToast = useRuntimeStore(state => state.pushToast);
  async function save() {
    try {
      await createProfile(draft);
      client.invalidateQueries();
      pushToast({ tone: "success", text: "Profile created." });
      onClose();
    } catch (error) {
      pushToast({ tone: "error", text: String((error as Error).message || error) });
    }
  }
  return (
    <Modal title="Add model profile" onClose={onClose}>
      <div className="form-grid">
        <TextField label="Profile id" value={draft.id} onChange={id => setDraft({ ...draft, id })} />
        <TextField label="Label" value={draft.label} onChange={label => setDraft({ ...draft, label })} />
        <SelectField label="Provider" value={draft.provider} onChange={nextProvider => {
          const matched = settings.providers.find(item => item.id === nextProvider);
          setDraft({ ...draft, provider: nextProvider, base_url: matched?.base_url || draft.base_url });
        }} options={settings.providers.map(item => ({ value: item.id, label: item.name }))} />
        <TextField label="Base URL" value={draft.base_url} onChange={base_url => setDraft({ ...draft, base_url })} />
        <TextField label="Model" value={draft.model} onChange={model => setDraft({ ...draft, model })} />
        <PasswordField label="API key" value={draft.api_key} onChange={api_key => setDraft({ ...draft, api_key })} />
        <NumberField label="Context window" value={draft.context_window} onChange={context_window => setDraft({ ...draft, context_window })} />
        <NumberField label="Max tokens" value={draft.max_tokens} onChange={max_tokens => setDraft({ ...draft, max_tokens })} />
      </div>
      <div className="toolbar modal-actions">
        <button className="secondary-button" onClick={onClose}>Cancel</button>
        <button className="primary-button" onClick={save} disabled={!draft.id.trim() || !draft.model.trim()}>Create profile</button>
      </div>
    </Modal>
  );
}

function RolesPanel({ settings }: { settings: SettingsViewModel }) {
  const [roles, setRoles] = useState<Record<string, string>>(settings.roles || {});
  const save = useSaveSection("roles");
  useEffect(() => setRoles(settings.roles || {}), [settings.roles]);
  const roleNames = ["chat", "plan", "compress", "fast"];
  return (
    <Panel title="Roles" kicker="Route internal tasks to dedicated profiles" action={<SaveButton onClick={() => save.mutate(roles)} />}>
      <div className="role-grid">
        {roleNames.map(role => (
          <SelectField
            key={role}
            label={role}
            value={roles[role] || ""}
            onChange={value => setRoles({ ...roles, [role]: value })}
            options={[{ value: "", label: "Default chat profile" }, ...settings.profiles.map(profile => ({ value: profile.id, label: profile.label || profile.id }))]}
          />
        ))}
      </div>
    </Panel>
  );
}

function AssistantPanel({ settings }: { settings: SettingsViewModel }) {
  const [draft, setDraft] = useState(settings.assistant);
  const save = useSaveSection("assistant");
  useEffect(() => setDraft(settings.assistant), [settings.assistant]);
  return (
    <Panel title="Assistant" kicker="Kai identity, behavior and context policy" action={<SaveButton onClick={() => save.mutate(draft)} />}>
      <div className="assistant-hero">
        <div className="avatar-stack"><span>K</span><span>A</span><span>I</span></div>
        <TextField label="Assistant name" value={draft.name} onChange={name => setDraft({ ...draft, name })} />
      </div>
      <TextareaField label="System prompt notes" value={draft.system_prompt} onChange={system_prompt => setDraft({ ...draft, system_prompt })} hint="Stored in config for future prompt customization; strict providers still receive the main system message first." />
      <div className="form-grid">
        <SelectField label="Default mode" value={draft.default_mode} onChange={default_mode => setDraft({ ...draft, default_mode })} options={[
          { value: "chat", label: "Chat" },
          { value: "plan", label: "Plan" },
          { value: "build", label: "Build" }
        ]} />
        <SelectField label="Authorization" value={draft.authorization_level} onChange={authorization_level => setDraft({ ...draft, authorization_level })} options={[
          { value: "manual", label: "Manual" },
          { value: "auto", label: "Auto" },
          { value: "yolo", label: "YOLO" }
        ]} />
      </div>
      <div className="settings-card-grid">
        <SwitchField label="Plan mode" checked={draft.plan_mode} onChange={plan_mode => setDraft({ ...draft, plan_mode })} />
        <SwitchField label="Thinking mode" checked={draft.thinking_mode} onChange={thinking_mode => setDraft({ ...draft, thinking_mode })} />
      </div>
      <ContextPolicyEditor value={draft.context_management} onChange={context_management => setDraft({ ...draft, context_management })} />
    </Panel>
  );
}

function ContextPolicyEditor({ value, onChange }: { value: Record<string, unknown>; onChange: (value: Record<string, unknown>) => void }) {
  const v = {
    enabled: Boolean(value.enabled ?? true),
    auto_compress: Boolean(value.auto_compress ?? true),
    trigger_percent: Number(value.trigger_percent ?? 85),
    target_percent: Number(value.target_percent ?? 60),
    preserve_recent_turns: Number(value.preserve_recent_turns ?? 4)
  };
  return (
    <div className="surface subtle">
      <div className="surface-header"><strong>Context management</strong></div>
      <div className="settings-card-grid">
        <SwitchField label="Enabled" checked={v.enabled} onChange={enabled => onChange({ ...v, enabled })} />
        <SwitchField label="Auto compress" checked={v.auto_compress} onChange={auto_compress => onChange({ ...v, auto_compress })} />
      </div>
      <div className="form-grid">
        <NumberField label="Trigger percent" value={v.trigger_percent} onChange={trigger_percent => onChange({ ...v, trigger_percent })} />
        <NumberField label="Target percent" value={v.target_percent} onChange={target_percent => onChange({ ...v, target_percent })} />
        <NumberField label="Preserve turns" value={v.preserve_recent_turns} onChange={preserve_recent_turns => onChange({ ...v, preserve_recent_turns })} />
      </div>
    </div>
  );
}

function MePanel({ settings }: { settings: SettingsViewModel }) {
  const [draft, setDraft] = useState(settings.user);
  const save = useSaveSection("user");
  useEffect(() => setDraft(settings.user), [settings.user]);
  return (
    <Panel title="Me" kicker="Local identity and preferences" action={<SaveButton onClick={() => save.mutate(draft)} />}>
      <div className="form-grid">
        <TextField label="Your name" value={draft.name} onChange={name => setDraft({ ...draft, name })} />
        <TextField label="Timezone" value={draft.timezone} onChange={timezone => setDraft({ ...draft, timezone })} placeholder="Asia/Shanghai" />
      </div>
      <TextareaField label="Preferences" value={draft.preferences} onChange={preferences => setDraft({ ...draft, preferences })} />
      <TextareaField label="Default instruction" value={draft.default_instruction} onChange={default_instruction => setDraft({ ...draft, default_instruction })} />
    </Panel>
  );
}

function WorkbenchPanel({ settings }: { settings: SettingsViewModel }) {
  const [draft, setDraft] = useState(settings.workbench);
  const save = useSaveSection("workbench");
  useEffect(() => setDraft(settings.workbench), [settings.workbench]);
  function setBookmark(index: number, patch: Partial<WorkspaceBookmark>) {
    setDraft({
      ...draft,
      workspace_bookmarks: draft.workspace_bookmarks.map((item, i) => i === index ? { ...item, ...patch } : item)
    });
  }
  return (
    <Panel title="Workbench" kicker="Project roots, scans and file review" action={<SaveButton onClick={() => save.mutate(draft)} />}>
      <div className="form-grid">
        <TextField label="Workspace root" value={draft.workspace_root} onChange={workspace_root => setDraft({ ...draft, workspace_root })} />
        <TextField label="Skills directory" value={draft.skills_dir} onChange={skills_dir => setDraft({ ...draft, skills_dir })} />
        <SelectField label="Shell" value={draft.shell_type} onChange={shell_type => setDraft({ ...draft, shell_type })} options={[
          { value: "powershell", label: "PowerShell" },
          { value: "cmd", label: "cmd.exe" },
          { value: "bash", label: "Bash" }
        ]} />
        <NumberField label="Max files" value={draft.workspace_max_files} onChange={workspace_max_files => setDraft({ ...draft, workspace_max_files })} />
        <NumberField label="Diff max bytes" value={draft.workspace_diff_max_bytes} onChange={workspace_diff_max_bytes => setDraft({ ...draft, workspace_diff_max_bytes })} />
        <NumberField label="Refresh seconds" value={draft.workspace_refresh_seconds} step={0.5} onChange={workspace_refresh_seconds => setDraft({ ...draft, workspace_refresh_seconds })} />
      </div>
      <div className="surface subtle">
        <div className="surface-header">
          <strong>Bookmarks</strong>
          <button className="secondary-button" onClick={() => setDraft({ ...draft, workspace_bookmarks: [...draft.workspace_bookmarks, { name: "New", path: "." }] })}><Plus size={14} /> Add</button>
        </div>
        {draft.workspace_bookmarks.map((bookmark, index) => (
          <div className="bookmark-row" key={`${bookmark.name}-${index}`}>
            <input value={bookmark.name} onChange={event => setBookmark(index, { name: event.target.value })} />
            <input value={bookmark.path} onChange={event => setBookmark(index, { path: event.target.value })} />
            <button className="icon-button danger" onClick={() => setDraft({ ...draft, workspace_bookmarks: draft.workspace_bookmarks.filter((_, i) => i !== index) })}><Trash2 size={15} /></button>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function SkillsPanel({ settings }: { settings: SettingsViewModel }) {
  const [draft, setDraft] = useState(settings.skills);
  const skills = useQuery({ queryKey: ["skills"], queryFn: getSkills });
  const save = useSaveSection("skills");
  const client = useQueryClient();
  const pushToast = useRuntimeStore(state => state.pushToast);
  useEffect(() => setDraft(settings.skills), [settings.skills]);
  async function reload() {
    try {
      await reloadSkills();
      client.invalidateQueries({ queryKey: ["skills"] });
      pushToast({ tone: "success", text: "Skills reloaded." });
    } catch (error) {
      pushToast({ tone: "error", text: String((error as Error).message || error) });
    }
  }
  return (
    <Panel title="Skills" kicker="Installed tools and skill policy" action={<><button className="secondary-button" onClick={reload}><RotateCw size={16} /> Reload</button><SaveButton onClick={() => save.mutate(draft)} /></>}>
      <div className="form-grid">
        <TextField label="Skills directory" value={draft.skills_dir} onChange={skills_dir => setDraft({ ...draft, skills_dir })} />
        <SwitchField label="Require skill hash" checked={draft.require_hash} onChange={require_hash => setDraft({ ...draft, require_hash })} />
      </div>
      <div className="skills-grid">
        {(skills.data?.tools || []).map(tool => (
          <article className="skill-card" key={tool.name}>
            <strong>{tool.name}</strong>
            <p>{tool.description || "No description"}</p>
            <Badge>{tool.permission}</Badge>
          </article>
        ))}
      </div>
    </Panel>
  );
}

function AppearancePanel({ settings }: { settings: SettingsViewModel }) {
  const [draft, setDraft] = useState(settings.appearance);
  const save = useSaveSection("appearance");
  useEffect(() => setDraft(settings.appearance), [settings.appearance]);
  return (
    <Panel title="Appearance" kicker="Theme, density and animation" action={<SaveButton onClick={() => save.mutate(draft)} />}>
      <div className="theme-preview">
        <div className="kai-preview">K</div>
        <div>
          <strong>Kairo Graphical Workbench</strong>
          <p>Dense enough for engineering, quiet enough for long sessions.</p>
        </div>
      </div>
      <div className="form-grid">
        <SelectField label="Web theme" value={draft.theme} onChange={theme => setDraft({ ...draft, theme })} options={[
          { value: "system", label: "System" },
          { value: "kairo-dark", label: "Kairo dark" },
          { value: "kairo-light", label: "Kairo light" }
        ]} />
        <SelectField label="Density" value={draft.density} onChange={density => setDraft({ ...draft, density })} options={[
          { value: "comfortable", label: "Comfortable" },
          { value: "compact", label: "Compact" },
          { value: "spacious", label: "Spacious" }
        ]} />
        <NumberField label="Font size" value={draft.font_size} onChange={font_size => setDraft({ ...draft, font_size })} />
        <SelectField label="Animation" value={draft.animation} onChange={animation => setDraft({ ...draft, animation })} options={[
          { value: "full", label: "Full" },
          { value: "reduced", label: "Reduced" },
          { value: "off", label: "Off" }
        ]} />
      </div>
      <div className="settings-card-grid">
        <SwitchField label="Kai mascot" checked={draft.mascot} onChange={mascot => setDraft({ ...draft, mascot })} />
        <SwitchField label="Reduced motion" checked={draft.reduced_motion} onChange={reduced_motion => setDraft({ ...draft, reduced_motion })} />
      </div>
    </Panel>
  );
}

function ImportExportPanel({ settings }: { settings: SettingsViewModel }) {
  const [exported, setExported] = useState("");
  const [importPath, setImportPath] = useState("");
  const [confirmKeys, setConfirmKeys] = useState(false);
  const pushToast = useRuntimeStore(state => state.pushToast);
  const client = useQueryClient();

  async function doExport(withKeys: boolean) {
    if (withKeys && !confirmKeys) {
      pushToast({ tone: "warn", text: "Enable the confirmation checkbox before exporting keys." });
      return;
    }
    try {
      const result = await exportConfig(withKeys, withKeys ? "EXPORT_KEYS" : "");
      setExported(safeJson(result.config));
    } catch (error) {
      pushToast({ tone: "error", text: String((error as Error).message || error) });
    }
  }

  async function doImport() {
    try {
      await importConfig(importPath);
      client.invalidateQueries();
      pushToast({ tone: "success", text: "Config imported." });
    } catch (error) {
      pushToast({ tone: "error", text: String((error as Error).message || error) });
    }
  }

  return (
    <Panel title="Import / Export" kicker="Redacted by default" action={<button className="secondary-button" onClick={() => setExported(safeJson(settings.raw))}>Show current redacted config</button>}>
      <div className="warning-box"><EyeOff size={16} /> Exports are redacted unless you explicitly confirm secret export.</div>
      <div className="toolbar">
        <button className="secondary-button" onClick={() => doExport(false)}>Export redacted</button>
        <label className="checkbox-row"><input type="checkbox" checked={confirmKeys} onChange={event => setConfirmKeys(event.target.checked)} /> I understand this includes API keys.</label>
        <button className="secondary-button danger" onClick={() => doExport(true)}>Export with keys</button>
      </div>
      <Field label="Import path">
        <div className="inline-form">
          <input value={importPath} onChange={event => setImportPath(event.target.value)} placeholder="C:\\path\\config.json" />
          <button className="primary-button" onClick={doImport} disabled={!importPath.trim()}>Import</button>
        </div>
      </Field>
      {exported ? <textarea className="json-editor advanced" readOnly value={exported} /> : null}
    </Panel>
  );
}

function Panel({ title, kicker, action, children }: { title: string; kicker: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="settings-section">
      <header className="page-header">
        <div>
          <span className="section-kicker">{kicker}</span>
          <h2>{title}</h2>
        </div>
        <div className="toolbar">{action}</div>
      </header>
      <div className="settings-stack">{children}</div>
    </section>
  );
}

function SaveButton({ onClick }: { onClick: () => void }) {
  return <button className="primary-button" onClick={onClick}><Save size={16} /> Save</button>;
}
