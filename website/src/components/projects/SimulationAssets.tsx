"use client";

import { useState } from "react";
import { useSession } from "@/components/configurations/Session";
import { notifySessionExpired } from "@/lib/configurations/session-events";
import { errorText } from "@/lib/platform/client";
import { useProjectResource, type RobotProfile } from "@/lib/projects/client";

interface Asset { engine: string; stored: boolean; size_bytes: number | null }
const size = (bytes: number) => bytes < 1024 ? `${bytes} bytes` : `${(bytes / 1024).toFixed(1)} KiB`;

export function SimulationAssets({ profile }: { profile: RobotProfile }) {
  const { operator } = useSession();
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState<string>();
  const [error, setError] = useState<string>();
  const assets = useProjectResource<Asset[]>(`robot-profiles/${profile.id}/simulation-assets`, revision);
  async function upload(engine: string, digest: string, file?: File) {
    if (!file || busy) return;
    setBusy(engine); setError(undefined);
    try {
      if (!file.size || file.size > 16 * 1024 * 1024) throw new Error("Choose a nonempty model file or ZIP bundle up to 16 MiB.");
      const bytes = await file.arrayBuffer();
      const actual = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), b => b.toString(16).padStart(2, "0")).join("");
      if (actual !== digest) throw new Error("This file does not match the saved profile. Select the pinned model file or import a new profile revision.");
      const response = await fetch(`/api/platform/robot-profiles/${profile.id}/simulation-assets/${engine}`, {
        method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/octet-stream", "X-Convoy-Client": "web" }, body: bytes,
      });
      if (response.status === 401) { notifySessionExpired(); return; }
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.error === "string" ? result.error : "Model upload failed. Retry with the same file.");
      setRevision(n => n + 1);
    } catch (cause) { setError(errorText(cause)); }
    finally { setBusy(undefined); }
  }
  return <section aria-label={`Simulation files for ${profile.name} revision ${profile.revision}`}>
    <h3>Simulation files</h3>
    <p>Upload the file pinned by this profile. The assigned runner downloads it when you verify or deploy the simulator. Uploading alone does not verify the model.</p>
    {(error || assets.error) && <p role="alert">{error ?? assets.error}</p>}
    {profile.spec.simulations.map(model => {
      const saved = assets.data?.find(a => a.engine === model.engine);
      return <div key={model.engine}>
        <p>{model.engine} · {model.asset.format} · {saved ? saved.stored ? `Stored · ${size(saved.size_bytes ?? 0)}` : "File needed" : "Checking stored file…"}</p>
        {operator && <label className="cv-field">Upload {model.engine} model<input type="file" disabled={!!busy} onChange={e => { void upload(model.engine, model.asset.sha256, e.target.files?.[0]); e.target.value = ""; }} /></label>}
        {busy === model.engine && <p role="status">Uploading model…</p>}
      </div>;
    })}
  </section>;
}
