"use client";

import Link from "next/link";
import { useId, useState, type FormEvent } from "react";
import { Badge, ConfigStatusBadge, Missing } from "@/components/configurations/Badges";
import { Modal } from "@/components/configurations/Overlay";
import { useSession } from "@/components/configurations/Session";
import { EmptyState, LoadingState } from "@/components/configurations/States";
import { currentRevision, useWorkspace } from "@/lib/configurations/client";
import { fmtCount } from "@/lib/configurations/format";
import { routes } from "@/lib/configurations/routes";
import type { Configuration } from "@/lib/configurations/types";
import { api, errorText, type Application } from "@/lib/platform/client";
import { configurationHref, newConfigurationHref } from "./ProjectConfigurations";
import { REFERENCE_RUNTIME } from "./ProjectNavigation";
import type { ProjectRobotConfigurationCatalogue } from "./ProjectRobotConfigurations";

const models = (items: ReadonlyArray<{ shortName: string }>) => items.map(item => item.shortName).join(" · ");

/**
 * A project's configurations: runnable ones (immutable releases deployed to its registered
 * robots) and the saved configurations assigned to it, each an equal card.
 */
export function ProjectConfigurationsSection({ projectId, catalogue }: { projectId: string; catalogue: ProjectRobotConfigurationCatalogue }) {
  const ws = useWorkspace();
  const { operator } = useSession();
  const [assigning, setAssigning] = useState(false);
  const configurations = ws.source === "document" ? ws.workspace?.configurations ?? [] : [];
  const saved = configurations.filter(config => config.projectId === projectId);
  const unassigned = configurations.filter(config => !config.projectId);
  return <>
    <section className="cv-row cv-row--first" aria-labelledby="pc-runnable">
      <div className="cv-row__head"><h2 id="pc-runnable">Runnable configurations</h2>
        {operator && <span className="cv-row__actions"><Link className="cv-btn cv-btn--secondary cv-btn--small" href={newConfigurationHref(projectId)} aria-label="New runnable configuration">New</Link></span>}</div>
      {catalogue.loading ? <LoadingState />
        : catalogue.applications.length ? <div className="cv-grid">{catalogue.applications.map(application => <ApplicationCard key={application.id} application={application} catalogue={catalogue} />)}</div>
          : <EmptyState title="No runnable configurations yet." />}
    </section>
    <section className="cv-row" aria-labelledby="pc-saved">
      <div className="cv-row__head"><h2 id="pc-saved">Saved configurations</h2>
        {ws.canSave && <span className="cv-row__actions">
          {unassigned.length > 0 && <button className="cv-btn cv-btn--secondary cv-btn--small" type="button" aria-label="Add a saved configuration" onClick={() => setAssigning(true)}>Add existing</button>}
          <Link className="cv-btn cv-btn--secondary cv-btn--small" href={`/app/configurations/new?project_id=${encodeURIComponent(projectId)}&setup=draft`} aria-label="New saved configuration">New</Link>
        </span>}</div>
      {ws.status === "loading" ? <LoadingState />
        : saved.length ? <div className="cv-grid">{saved.map(config => <SavedCard key={config.id} config={config} robots={ws.workspace?.robots.filter(robot => robot.configId === config.id).length ?? 0} />)}</div>
          : <EmptyState title="No saved configurations in this project." />}
    </section>
    {assigning && <AssignDialog projectId={projectId} unassigned={unassigned} onClose={() => setAssigning(false)} />}
  </>;
}

/** A runnable configuration: its latest release's task, policy and timing, and where it is deployed. */
function ApplicationCard({ application, catalogue }: { application: Application; catalogue: ProjectRobotConfigurationCatalogue }) {
  const releases = catalogue.releases.filter(release => release.application_id === application.id);
  const latest = releases[0];
  const manifest = latest?.manifest.schema_version === 3 ? latest.manifest : undefined;
  const task = latest?.manifest.schema_version === 1 ? null : latest?.manifest.task.instruction ?? null;
  const deployed = catalogue.deployments.filter(deployment => releases.some(release => release.id === deployment.release_id)).length;
  return <Link className="cv-config" href={configurationHref(application.id)}>
    <div className="cv-config__head"><h2>{application.name}</h2>{deployed ? <Badge tone="success">Deployed</Badge> : <Badge>Not deployed</Badge>}</div>
    <dl className="cv-config__facts">
      <div><dt>Task</dt><dd>{task ?? <Missing />}</dd></div>
      <div><dt>Policy</dt><dd>{manifest ? manifest.policy.runtime === REFERENCE_RUNTIME ? "Reference controller" : manifest.policy.runtime : latest?.manifest.profile ?? <Missing />}</dd></div>
      <div><dt>Timing</dt><dd>{manifest ? manifest.execution.timing ? "Measured real time" : "Functional" : <Missing />}</dd></div>
      <div><dt>Releases</dt><dd>{fmtCount(releases.length)}</dd></div>
    </dl>
    <div className="cv-config__foot"><span>{deployed ? `${deployed} ${deployed === 1 ? "robot" : "robots"}` : "No robots"}</span>{latest && <span>Release <span className="cv-mono">{latest.digest.slice(0, 8)}</span></span>}</div>
  </Link>;
}

/** A saved configuration assigned to the project, as on the Configurations page. */
function SavedCard({ config, robots }: { config: Configuration; robots: number }) {
  const revision = currentRevision(config);
  return <Link className="cv-config" href={routes.configuration(config.id)}>
    <div className="cv-config__head"><h2>{config.name}</h2><ConfigStatusBadge status={config.status} /></div>
    <dl className="cv-config__facts">
      <div><dt>Robot</dt><dd>{revision.robot.name}</dd></div>
      <div><dt>Edge</dt><dd>{revision.edgeHardware.name}</dd></div>
      <div><dt>Edge model</dt><dd>{models(revision.edgeModels) || "–"}</dd></div>
      <div><dt>Cloud model</dt><dd>{models(revision.cloudModels) || "–"}</dd></div>
    </dl>
    <div className="cv-config__foot"><span>{robots === 1 ? "1 robot" : `${robots} robots`}</span></div>
  </Link>;
}

/** Assign a saved configuration that has no project. Its robots, evals and replays stay as they are; nothing is deployed. */
function AssignDialog({ projectId, unassigned, onClose }: { projectId: string; unassigned: Configuration[]; onClose: () => void }) {
  const ws = useWorkspace();
  const id = useId();
  const [selected, setSelected] = useState(unassigned.length === 1 ? unassigned[0].id : "");
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  async function assign(event: FormEvent) {
    event.preventDefault();
    if (!selected) return;
    setBusy(true); setError(undefined);
    try {
      const links = await api<{ application: { project_id: string } }[]>(`workspace-configuration-links?configuration_id=${encodeURIComponent(selected)}`);
      if (links.some(link => link.application.project_id !== projectId)) throw new Error("This configuration is linked to a runnable configuration in another project. Review that link first.");
      const result = await ws.save(current => {
        const config = current.configurations.find(item => item.id === selected);
        if (!config || config.projectId) throw new Error("This configuration has changed. Reload before adding it.");
        return { ...current, configurations: current.configurations.map(item => item.id === selected ? { ...item, projectId } : item) };
      });
      if (!result.ok) throw new Error(result.error);
      onClose();
    } catch (cause) { setError(errorText(cause)); }
    finally { setBusy(false); }
  }
  return <Modal open onClose={onClose} title="Add a saved configuration" footer={<>
    <button className="cv-btn cv-btn--secondary" type="button" onClick={onClose}>Cancel</button>
    <button className="cv-btn cv-btn--primary" type="submit" form={`${id}-form`} disabled={!selected || busy || ws.saving}>{busy ? "Adding…" : "Add to project"}</button>
  </>}>
    <form id={`${id}-form`} className="cv-form cv-form--dialog" onSubmit={event => void assign(event)}>
      <label className="cv-field">Saved configuration<select className="cv-input" value={selected} disabled={busy} onChange={event => setSelected(event.target.value)} data-autofocus="">
        <option value="">Choose a saved configuration</option>{unassigned.map(config => <option key={config.id} value={config.id}>{config.name}</option>)}
      </select></label>
      {error && <p className="cv-form-error" role="alert">{error}</p>}
    </form>
  </Modal>;
}
