"use client";
import Link from "next/link";
import { useState } from "react";
import { useWorkspace, currentRevision } from "@/lib/configurations/client";
import { routes } from "@/lib/configurations/routes";
import { api, errorText } from "@/lib/platform/client";

export function SavedProjectConfigurations({ projectId }: { projectId: string }) {
  const ws = useWorkspace();
  const [selected, setSelected] = useState("");
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const configurations = ws.source === "document" ? ws.workspace?.configurations ?? [] : [];
  async function assign() {
    setBusy(true); setError(undefined);
    try {
      const links = await api<{ application: { project_id: string } }[]>(`workspace-configuration-links?configuration_id=${encodeURIComponent(selected)}`);
      if (links.some(link => link.application.project_id !== projectId)) throw new Error("This setup has an execution link in another project. Review that link before assigning it here.");
      const result = await ws.save(current => {
        const config = current.configurations.find(c => c.id === selected);
        if (!config || config.projectId) throw new Error("This setup has changed. Reload before assigning it.");
        return { ...current, configurations: current.configurations.map(c => c.id === selected ? { ...c, projectId } : c) };
      });
      if (!result.ok) throw new Error(result.error);
      setSelected("");
    } catch (cause) { setError(errorText(cause)); }
    finally { setBusy(false); }
  }
  const unassigned = configurations.filter(c => !c.projectId);
  return <section className="project-section" aria-label="Model setups"><h2>Model setups and evaluations</h2>
    <p>Plan edge and cloud models here. A saved setup requires a verified execution integration before deployment.</p>
    <p><Link className="cv-btn cv-btn--secondary" href={`/app/configurations/new?project_id=${projectId}&setup=draft`}>Create model setup</Link></p>
    {ws.status === "loading" && <p role="status">Loading saved setups…</p>}
    <div className="cv-grid">{configurations.filter(c => c.projectId === projectId).map(config => {
      const revision = currentRevision(config);
      return <Link className="cv-config" href={routes.configuration(config.id)} key={config.id}><h3>{config.name}</h3><p>Saved model setup · {revision.robot.name}</p><p>Edge: {revision.edgeModels.map(m => m.shortName).join(", ") || "None selected"}</p><p>Cloud: {revision.cloudModels.map(m => m.shortName).join(", ") || "None selected"}</p><p>Open setup, linked deployment and evaluations →</p></Link>;
    })}</div>
    {ws.canSave && unassigned.length > 0 && <form className="cv-form project-create" onSubmit={event => { event.preventDefault(); void assign(); }}><h3>Bring an existing setup into this project</h3><p>Its saved robots, evaluations and replay links stay intact. This does not deploy software.</p><label className="cv-field">Unassigned setup<select className="cv-input" value={selected} disabled={busy || ws.saving} onChange={event => setSelected(event.target.value)}><option value="">Choose a saved setup</option>{unassigned.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label><button className="cv-btn cv-btn--secondary" disabled={!selected || busy || ws.saving}>Assign to project</button></form>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
