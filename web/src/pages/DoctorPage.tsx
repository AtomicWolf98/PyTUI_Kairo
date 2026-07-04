import React, { useState } from "react";
import { CheckCircle2, HeartPulse, PlayCircle, XCircle } from "lucide-react";
import { runDoctor } from "../api";
import { Badge, EmptyState, safeJson } from "../components";
import { useRuntimeStore } from "../stores";
import type { DoctorResult } from "../types";

export function DoctorPage() {
  const [result, setResult] = useState<DoctorResult | null>(null);
  const [running, setRunning] = useState(false);
  const pushToast = useRuntimeStore(state => state.pushToast);

  async function run(localOnly = true) {
    setRunning(true);
    try {
      const next = await runDoctor(localOnly);
      setResult(next);
      pushToast({ tone: next.ok ? "success" : "error", text: next.message || "Doctor finished." });
    } catch (error) {
      pushToast({ tone: "error", text: String((error as Error).message || error) });
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="page doctor-page">
      <header className="page-header">
        <div>
          <span className="section-kicker">Doctor</span>
          <h2>Health Dashboard</h2>
        </div>
        <button className="primary-button" onClick={() => run(true)} disabled={running}>
          <PlayCircle size={16} /> {running ? "Running" : "Run checks"}
        </button>
      </header>

      {!result ? (
        <EmptyState title="Run a local health check" detail="Kairo will inspect config, keys, workspace, sessions, skills, git and provider reachability without exposing secrets." />
      ) : (
        <div className="doctor-grid">
          <section className={result.ok ? "doctor-summary ok" : "doctor-summary fail"}>
            <HeartPulse size={24} />
            <div>
              <strong>{result.ok ? "All clear" : "Action needed"}</strong>
              <p>{result.message}</p>
            </div>
          </section>
          {(result.checks || []).map((check, index) => {
            const ok = Boolean(check.ok);
            const name = String(check.name || `Check ${index + 1}`);
            const detail = String(check.detail || check.message || "");
            return (
              <article className="check-card" key={`${name}-${index}`}>
                <div>
                  {ok ? <CheckCircle2 className="good-icon" size={18} /> : <XCircle className="bad-icon" size={18} />}
                  <strong>{name}</strong>
                  <Badge tone={ok ? "good" : "bad"}>{ok ? "ok" : "fail"}</Badge>
                </div>
                <p>{detail || safeJson(check)}</p>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
