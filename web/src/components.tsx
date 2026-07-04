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
