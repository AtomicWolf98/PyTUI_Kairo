import React, { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { EyeOff, KeyRound, RotateCw, Save } from "lucide-react";
import { exportConfig, getConfig, getSkills, importConfig, patchConfig, reloadSkills, switchProfile } from "../api";
import { Badge, EmptyState, Field, Modal, safeJson } from "../components";
import { useRuntimeStore } from "../stores";
import type { ConfigProfile, ConfigViewModel } from "../types";

type SettingsTab = "profiles" | "keys" | "roles" | "appearance" | "assistant" | "workbench" | "skills" | "export";

const tabs: Array<{ id: SettingsTab; label: string }> = [
  { id: "profiles", label: "Providers & Models" },
  { id: "keys", label: "Keys" },
  { id: "roles", label: "Roles" },
  { id: "appearance", label: "Appearance" },
  { id: "assistant", label: "Assistant" },
  { id: "workbench", label: "Workbench" },
  { id: "skills", label: "Skills" },
  { id: "export", label: "Import / Export" }
];

export function SettingsPage() {
  const [tab, setTab] = useState<SettingsTab>("profiles");
  const config = useQuery({ queryKey: ["config"], queryFn: getConfig });

  return (
    <div className="page settings-page">
      <header className="page-header">
        <div>
          <span className="section-kicker">Settings</span>
          <h2>Runtime Control Center</h2>
        </div>
        <Badge tone="info">{config.data?.llm.active_profile || "profile"}</Badge>
      </header>

      <div className="settings-layout">
        <nav className="settings-tabs">
          {tabs.map(item => (
            <button className={tab === item.id ? "active" : ""} key={item.id} onClick={() => setTab(item.id)}>
              {item.label}
            </button>
          ))}
        </nav>
        <section className="settings-panel">
          {!config.data ? <EmptyState title="Loading settings" /> : <SettingsTabView tab={tab} config={config.data} />}
        </section>
      </div>
    </div>
  );
}

function SettingsTabView({ tab, config }: { tab: SettingsTab; config: ConfigViewModel }) {
  if (tab === "keys") return <KeysPanel config={config} />;
  if (tab === "roles") return <RolesPanel config={config} />;
  if (tab === "appearance") return <AppearancePanel config={config} />;
  if (tab === "assistant") return <AssistantPanel config={config} />;
  if (tab === "workbench") return <WorkbenchPanel config={config} />;
  if (tab === "skills") return <SkillsPanel />;
  if (tab === "export") return <ImportExportPanel />;
  return <ProfilesPanel config={config} />;
}

function ProfilesPanel({ config }: { config: ConfigViewModel }) {
  const [profiles, setProfiles] = useState<ConfigProfile[]>(config.llm.profiles || []);
  const client = useQueryClient();
  const pushToast = useRuntimeStore(state => state.pushToast);

  useEffect(() => setProfiles(config.llm.profiles || []), [config.llm.profiles]);

  const save = useMutation({
    mutationFn: () => patchConfig("llm", { active_profile: config.llm.active_profile, profiles }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["config"] });
      pushToast({ tone: "success", text: "Profiles saved." });
    },
    onError: error => pushToast({ tone: "error", text: String((error as Error).message || error) })
  });

  async function activate(id: string) {
    try {
      const result = await switchProfile(id);
      pushToast({ tone: result.ok ? "success" : "error", text: result.message });
      client.invalidateQueries();
    } catch (error) {
      pushToast({ tone: "error", text: String((error as Error).message || error) });
    }
  }

  return (
    <div className="settings-stack">
      <div className="surface-header">
        <strong>OpenAI-compatible profiles</strong>
        <button className="primary-button" onClick={() => save.mutate()}><Save size={16} /> Save profiles</button>
      </div>
      {profiles.map((profile, index) => (
        <ProfileEditor
          key={profile.id}
          profile={profile}
          active={profile.id === config.llm.active_profile}
          onActivate={() => activate(profile.id)}
          onChange={next => setProfiles(profiles.map((item, i) => i === index ? next : item))}
        />
      ))}
    </div>
  );
}

function ProfileEditor({
  profile,
  active,
  onChange,
  onActivate
}: {
  profile: ConfigProfile;
  active: boolean;
  onChange: (profile: ConfigProfile) => void;
  onActivate: () => void;
}) {
  return (
    <article className="profile-editor">
      <div className="profile-editor-title">
        <div>
          <strong>{profile.label || profile.id}</strong>
          <p>{profile.provider} · {profile.model}</p>
        </div>
        <button className={active ? "secondary-button active" : "secondary-button"} onClick={onActivate}>
          {active ? "Active" : "Use"}
        </button>
      </div>
      <div className="form-grid">
        <Field label="Label"><input value={profile.label || ""} onChange={event => onChange({ ...profile, label: event.target.value })} /></Field>
        <Field label="Provider"><input value={profile.provider} onChange={event => onChange({ ...profile, provider: event.target.value })} /></Field>
        <Field label="Base URL"><input value={profile.base_url} onChange={event => onChange({ ...profile, base_url: event.target.value })} /></Field>
        <Field label="Model"><input value={profile.model} onChange={event => onChange({ ...profile, model: event.target.value })} /></Field>
        <Field label="Temperature"><input type="number" step="0.1" value={profile.temperature} onChange={event => onChange({ ...profile, temperature: Number(event.target.value) })} /></Field>
        <Field label="Max tokens"><input type="number" value={profile.max_tokens} onChange={event => onChange({ ...profile, max_tokens: Number(event.target.value) })} /></Field>
        <Field label="Context window"><input type="number" value={profile.context_window} onChange={event => onChange({ ...profile, context_window: Number(event.target.value) })} /></Field>
        <Field label="API key env"><input value={profile.api_key_env || ""} onChange={event => onChange({ ...profile, api_key_env: event.target.value })} /></Field>
      </div>
    </article>
  );
}

function KeysPanel({ config }: { config: ConfigViewModel }) {
  const [profileId, setProfileId] = useState(config.llm.active_profile || config.llm.profiles?.[0]?.id || "");
  const [key, setKey] = useState("");
  const client = useQueryClient();
  const pushToast = useRuntimeStore(state => state.pushToast);
  const profiles = config.profiles_summary || config.llm.profiles || [];

  async function saveKey(action: "set" | "clear") {
    if (action === "set" && !window.confirm("Write this API key to local config.json?")) return;
    try {
      await patchConfig("key", { profile_id: profileId, action, api_key: action === "set" ? key : "" });
      setKey("");
      client.invalidateQueries({ queryKey: ["config"] });
      pushToast({ tone: "success", text: action === "set" ? "Key saved." : "Key cleared." });
    } catch (error) {
      pushToast({ tone: "error", text: String((error as Error).message || error) });
    }
  }

  return (
    <div className="settings-stack">
      <div className="warning-box"><EyeOff size={16} /> Keys are masked everywhere except the local file you explicitly write.</div>
      <Field label="Profile">
        <select value={profileId} onChange={event => setProfileId(event.target.value)}>
          {profiles.map(profile => <option key={profile.id} value={profile.id}>{profile.label || profile.id}</option>)}
        </select>
      </Field>
      <Field label="New API key">
        <input type="password" value={key} onChange={event => setKey(event.target.value)} placeholder="sk-..." />
      </Field>
      <div className="toolbar">
        <button className="primary-button" onClick={() => saveKey("set")} disabled={!profileId || !key}>
          <KeyRound size={16} /> Save key
        </button>
        <button className="secondary-button danger" onClick={() => saveKey("clear")} disabled={!profileId}>Clear key</button>
      </div>
      <pre className="code-block">{safeJson(profiles.map(profile => ({ id: profile.id, key: profile.api_key, source: profile.api_key_source })))}</pre>
    </div>
  );
}

function RolesPanel({ config }: { config: ConfigViewModel }) {
  const profiles = config.llm.profiles || [];
  const [roles, setRoles] = useState<Record<string, string>>(config.model_roles || {});
  const pushToast = useRuntimeStore(state => state.pushToast);
  const client = useQueryClient();
  const names = ["chat", "plan", "compress", "fast"];

  async function save() {
    try {
      await patchConfig("roles", roles);
      client.invalidateQueries({ queryKey: ["config"] });
      pushToast({ tone: "success", text: "Roles saved." });
    } catch (error) {
      pushToast({ tone: "error", text: String((error as Error).message || error) });
    }
  }

  return (
    <div className="settings-stack">
      {names.map(role => (
        <Field label={role} key={role}>
          <select value={roles[role] || ""} onChange={event => setRoles({ ...roles, [role]: event.target.value })}>
            <option value="">Default</option>
            {profiles.map(profile => <option value={profile.id} key={profile.id}>{profile.label || profile.id}</option>)}
          </select>
        </Field>
      ))}
      <button className="primary-button" onClick={save}><Save size={16} /> Save roles</button>
    </div>
  );
}

function AppearancePanel({ config }: { config: ConfigViewModel }) {
  return <JsonSection section="ui" initial={config.ui || {}} title="Appearance and TUI behavior" />;
}

function AssistantPanel({ config }: { config: ConfigViewModel }) {
  const initial = {
    authorization_level: config.authorization_level || "manual",
    plan_mode: Boolean(config.plan_mode),
    thinking_mode: Boolean(config.thinking_mode),
    context_management: config.context_management || {}
  };
  return <JsonSection section="assistant" initial={initial} title="Assistant behavior" />;
}

function WorkbenchPanel({ config }: { config: ConfigViewModel }) {
  const initial = {
    workspace_root: config.workspace_root || ".",
    skills_dir: config.skills_dir || "./skills",
    shell_type: config.shell_type || "cmd",
    workspace_bookmarks: config.workspace_bookmarks || []
  };
  return <JsonSection section="workbench" initial={initial} title="Workbench" />;
}

function JsonSection({ section, initial, title }: { section: string; initial: Record<string, unknown>; title: string }) {
  const [text, setText] = useState(() => safeJson(initial));
  const pushToast = useRuntimeStore(state => state.pushToast);
  const client = useQueryClient();

  useEffect(() => setText(safeJson(initial)), [initial]);

  async function save() {
    try {
      const payload = JSON.parse(text) as Record<string, unknown>;
      await patchConfig(section, payload);
      client.invalidateQueries({ queryKey: ["config"] });
      pushToast({ tone: "success", text: `${title} saved.` });
    } catch (error) {
      pushToast({ tone: "error", text: String((error as Error).message || error) });
    }
  }

  return (
    <div className="settings-stack">
      <div className="surface-header">
        <strong>{title}</strong>
        <button className="primary-button" onClick={save}><Save size={16} /> Save</button>
      </div>
      <textarea className="json-editor" value={text} onChange={event => setText(event.target.value)} />
    </div>
  );
}

function SkillsPanel() {
  const skills = useQuery({ queryKey: ["skills"], queryFn: getSkills });
  const client = useQueryClient();
  const pushToast = useRuntimeStore(state => state.pushToast);

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
    <div className="settings-stack">
      <button className="secondary-button" onClick={reload}><RotateCw size={16} /> Reload skills</button>
      <div className="skills-grid">
        {(skills.data?.tools || []).map(tool => (
          <article className="skill-card" key={tool.name}>
            <strong>{tool.name}</strong>
            <p>{tool.description || "No description"}</p>
            <Badge>{tool.permission}</Badge>
          </article>
        ))}
      </div>
    </div>
  );
}

function ImportExportPanel() {
  const [exported, setExported] = useState("");
  const [importPath, setImportPath] = useState("");
  const [confirmKeys, setConfirmKeys] = useState(false);
  const [showKeysModal, setShowKeysModal] = useState(false);
  const pushToast = useRuntimeStore(state => state.pushToast);
  const client = useQueryClient();

  async function doExport(withKeys: boolean) {
    if (withKeys && !confirmKeys) {
      setShowKeysModal(true);
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
    if (!window.confirm("Import this config file and create a backup of the current config?")) return;
    try {
      await importConfig(importPath);
      client.invalidateQueries({ queryKey: ["config"] });
      pushToast({ tone: "success", text: "Config imported." });
    } catch (error) {
      pushToast({ tone: "error", text: String((error as Error).message || error) });
    }
  }

  return (
    <div className="settings-stack">
      <div className="toolbar">
        <button className="secondary-button" onClick={() => doExport(false)}>Export redacted</button>
        <button className="secondary-button danger" onClick={() => doExport(true)}>Export with keys</button>
      </div>
      <Field label="Import path">
        <div className="inline-form">
          <input value={importPath} onChange={event => setImportPath(event.target.value)} placeholder="C:\\path\\config.json" />
          <button className="primary-button" onClick={doImport} disabled={!importPath.trim()}>Import</button>
        </div>
      </Field>
      {exported ? <textarea className="json-editor" value={exported} onChange={event => setExported(event.target.value)} /> : null}
      {showKeysModal ? (
        <Modal title="Export secrets?" onClose={() => setShowKeysModal(false)}>
          <div className="settings-stack">
            <p>This export will include inline API keys. Keep it local and do not commit it.</p>
            <label className="checkbox-row">
              <input type="checkbox" checked={confirmKeys} onChange={event => setConfirmKeys(event.target.checked)} />
              I understand this includes secrets.
            </label>
            <button className="primary-button danger" disabled={!confirmKeys} onClick={() => {
              setShowKeysModal(false);
              void doExport(true);
            }}>Export with keys</button>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
