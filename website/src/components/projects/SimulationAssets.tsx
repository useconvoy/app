"use client";

import { useState } from "react";
import { Icon } from "@/components/configurations/Icons";
import { useSession } from "@/components/configurations/Session";
import { notifySessionExpired } from "@/lib/configurations/session-events";
import { errorText } from "@/lib/platform/client";
import { useProjectResource, type RobotProfile } from "@/lib/projects/client";
import { engineLabel } from "./ProjectNavigation";

interface Asset { engine: string; stored: boolean; size_bytes: number | null }
const size = (bytes: number) => bytes < 1024 ? `${bytes} bytes` : `${(bytes / 1024).toFixed(1)} KiB`;

/** The model file each simulator engine needs, as pinned by the profile: stored or needed, and an upload. */
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
  return <section className="cv-assets" aria-label={`Simulation files for ${profile.name} revision ${profile.revision}`}>
    <h3>Simulation files</h3>
    {(error || assets.error) && <p className="cv-form-error" role="alert">{error ?? assets.error}</p>}
    {profile.spec.simulations.map(model => {
      const saved = assets.data?.find(asset => asset.engine === model.engine);
      return <div className="cv-asset" key={model.engine}>
        <span>{engineLabel(model.engine)} · {model.asset.format.toUpperCase()} · {busy === model.engine ? "Uploading…" : saved ? saved.stored ? `Stored · ${size(saved.size_bytes ?? 0)}` : "File needed" : "Checking stored file…"}</span>
        {operator && <label className="cv-btn cv-btn--secondary cv-btn--small cv-file"><Icon name="upload" />Upload
          <input type="file" aria-label={`Upload ${engineLabel(model.engine)} model`} disabled={!!busy} onChange={event => { void upload(model.engine, model.asset.sha256, event.target.files?.[0]); event.target.value = ""; }} /></label>}
      </div>;
    })}
  </section>;
}
