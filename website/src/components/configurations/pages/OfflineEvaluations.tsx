"use client";

import { useState } from "react";
import { useWorkspace } from "@/lib/configurations/client";
import { fmtCount, fmtDate } from "@/lib/configurations/format";
import { linkOfflineEvaluations } from "@/lib/configurations/mutations";
import type { Robot } from "@/lib/configurations/types";
import { Modal } from "../Overlay";
import { useOfflineEvaluations } from "../platform";

/**
 * The account's offline evaluations as checkboxes: name, then task, episodes and import date.
 * Offline evaluations are imported with `integrations/simulation/scripts/import_offline_eval.py`.
 */
export function OfflinePicker({ value, onChange, disabled, describedBy }: {
  value: readonly string[]; onChange: (ids: string[]) => void; disabled?: boolean; describedBy?: string;
}) {
  const { state, retry } = useOfflineEvaluations(true);
  if (state.status === "error") return <p className="cv-field__error" role="alert">{state.message} <button className="cv-link" type="button" onClick={retry}>Retry</button></p>;
  if (state.status !== "ready") return <p className="cv-muted">Loading offline evals…</p>;
  if (!state.data.length) return <p className="cv-muted">No offline evals yet.</p>;
  const toggle = (id: string, on: boolean) => onChange(on ? [...value, id] : value.filter(item => item !== id));
  return <div className="cv-checks" role="group" aria-label="Offline evals" aria-describedby={describedBy}>
    {state.data.map(item => <label key={item.id} className="cv-check">
      <input type="checkbox" checked={value.includes(item.id)} disabled={disabled} onChange={event => toggle(item.id, event.target.checked)} />
      <span className="cv-check__name">{item.name}</span>
      <span className="cv-check__meta">{item.task} · {fmtCount(item.summary.episodes)} {item.summary.episodes === 1 ? "episode" : "episodes"} · {fmtDate(item.created_at)}</span>
    </label>)}
  </div>;
}

/** Choose which offline evaluations a robot shows as evals. */
export function LinkOfflineDialog({ robot, onClose }: { robot: Robot; onClose: () => void }) {
  const ws = useWorkspace();
  const [ids, setIds] = useState<string[]>(() => robot.offlineEvaluationIds ?? []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function save() {
    setBusy(true); setError(null);
    const result = await ws.save(current => linkOfflineEvaluations(current, robot.id, ids, Date.now()));
    setBusy(false);
    if (result.ok) onClose(); else setError(result.error);
  }
  return <Modal open onClose={onClose} title="Offline evals"
    footer={<>
      <button className="cv-btn cv-btn--secondary" type="button" onClick={onClose}>Cancel</button>
      <button className="cv-btn cv-btn--primary" type="button" disabled={busy} onClick={() => void save()}>{busy ? "Saving…" : "Save"}</button>
    </>}>
    <OfflinePicker value={ids} onChange={setIds} disabled={busy} />
    {error && <p className="cv-form-error" role="alert">{error}</p>}
  </Modal>;
}
