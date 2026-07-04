import React from "react";
import { X } from "lucide-react";
import { useRuntimeStore } from "./stores";

export function Badge({ children, tone = "neutral" }: { children: React.ReactNode; tone?: "neutral" | "good" | "warn" | "bad" | "info" }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function Meter({ value, max, label }: { value: number; max: number; label: string }) {
  const percent = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0;
  const tone = percent >= 85 ? "bad" : percent >= 60 ? "warn" : "good";
  return (
    <div className="meter" aria-label={label}>
      <div className="meter-label">
        <span>{label}</span>
        <strong>{Math.round(percent)}%</strong>
      </div>
      <div className={`meter-track meter-${tone}`}>
        <span style={{ width: `${percent}%` }} />
      </div>
    </div>
  );
}

export function EmptyState({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="empty-state">
      <strong>{title}</strong>
      {detail ? <p>{detail}</p> : null}
    </div>
  );
}

export function Field({
  label,
  children,
  hint
}: {
  label: string;
  children: React.ReactNode;
  hint?: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint ? <small>{hint}</small> : null}
    </label>
  );
}

export function TextField({
  label,
  value,
  onChange,
  placeholder,
  hint
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  hint?: string;
}) {
  return (
    <Field label={label} hint={hint}>
      <input value={value} placeholder={placeholder} onChange={event => onChange(event.target.value)} />
    </Field>
  );
}

export function PasswordField({
  label,
  value,
  onChange,
  placeholder,
  hint
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  hint?: string;
}) {
  return (
    <Field label={label} hint={hint}>
      <input type="password" value={value} placeholder={placeholder} onChange={event => onChange(event.target.value)} />
    </Field>
  );
}

export function TextareaField({
  label,
  value,
  onChange,
  placeholder,
  hint
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  hint?: string;
}) {
  return (
    <Field label={label} hint={hint}>
      <textarea value={value} placeholder={placeholder} onChange={event => onChange(event.target.value)} />
    </Field>
  );
}

export function NumberField({
  label,
  value,
  onChange,
  min,
  step = 1,
  hint
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  min?: number;
  step?: number;
  hint?: string;
}) {
  return (
    <Field label={label} hint={hint}>
      <input type="number" value={value} min={min} step={step} onChange={event => onChange(Number(event.target.value))} />
    </Field>
  );
}

export function SelectField({
  label,
  value,
  onChange,
  options,
  hint
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: Array<{ value: string; label: string }>;
  hint?: string;
}) {
  return (
    <Field label={label} hint={hint}>
      <select value={value} onChange={event => onChange(event.target.value)}>
        {options.map(option => <option value={option.value} key={option.value}>{option.label}</option>)}
      </select>
    </Field>
  );
}

export function SwitchField({
  label,
  checked,
  onChange,
  hint
}: {
  label: string;
  checked: boolean;
  onChange: (value: boolean) => void;
  hint?: string;
}) {
  return (
    <label className="switch-field">
      <span>
        <strong>{label}</strong>
        {hint ? <small>{hint}</small> : null}
      </span>
      <input type="checkbox" checked={checked} onChange={event => onChange(event.target.checked)} />
    </label>
  );
}

export function ConfirmDialog({
  title,
  detail,
  confirmLabel = "Confirm",
  danger = false,
  onConfirm,
  onClose
}: {
  title: string;
  detail: string;
  confirmLabel?: string;
  danger?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  return (
    <Modal title={title} onClose={onClose}>
      <div className="settings-stack">
        <p>{detail}</p>
        <div className="toolbar">
          <button className="secondary-button" onClick={onClose}>Cancel</button>
          <button className={danger ? "primary-button danger" : "primary-button"} onClick={onConfirm}>{confirmLabel}</button>
        </div>
      </div>
    </Modal>
  );
}

export function Modal({
  title,
  children,
  onClose
}: {
  title: string;
  children: React.ReactNode;
  onClose: () => void;
}) {
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="modal" role="dialog" aria-modal="true" aria-label={title} onMouseDown={event => event.stopPropagation()}>
        <header className="modal-header">
          <h3>{title}</h3>
          <button className="icon-button" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}

export function Toasts() {
  const toasts = useRuntimeStore(state => state.toasts);
  const dismiss = useRuntimeStore(state => state.dismissToast);
  return (
    <div className="toasts">
      {toasts.map(toast => (
        <button className={`toast toast-${toast.tone}`} key={toast.id} onClick={() => dismiss(toast.id)}>
          {toast.text}
        </button>
      ))}
    </div>
  );
}

export function formatNumber(value: number | undefined) {
  return typeof value === "number" ? value.toLocaleString() : "0";
}

export function safeJson(value: unknown) {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}
