"use client";

import Link from "next/link";
import { useState } from "react";
import { useSession } from "@/components/configurations/Session";
import { Card } from "@/components/configurations/Tiles";
import { ApiError, MutationAttempts, errorText, type Application, type Project } from "@/lib/platform/client";
import { notifySessionExpired } from "@/lib/configurations/session-events";
import { useProjectResource } from "@/lib/projects/client";
import { configurationHref, newConfigurationHref } from "./ProjectConfigurations";

interface WorkspaceLink {
  id: string; configuration_id: string; source_name: string; source_revision: number;
  document_revision: number; source_state: "unchanged" | "changed" | "missing" | "unsupported";
  application: Application; project_name: string;
}

const stateText = (link: WorkspaceLink) => link.source_state === "unchanged"
  ? "Saved specification unchanged since linking."
  : link.source_state === "changed" ? "The saved specification has changed. Review its differences before creating a new executable release. Existing releases and deployments are unchanged."
  : link.source_state === "missing" ? "The saved specification has been removed. Executable releases and deployments are unchanged."
  : "The saved specification cannot be read in its current format. Executable releases and deployments are unchanged.";

export function WorkspaceConfigurationLink({ configurationId, documentRevision, saving }: { configurationId: string; documentRevision: number; saving: boolean }) {
  const { operator } = useSession();
  const [revision, setRevision] = useState(0);
  const [attempts] = useState(() => new MutationAttempts());
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const links = useProjectResource<WorkspaceLink[]>(`workspace-configuration-links?configuration_id=${encodeURIComponent(configurationId)}`, revision + documentRevision);
  const link = links.data?.[0];
  async function mutate(path: string, body: unknown) {
    setBusy(true); setError(undefined);
    try { await attempts.submit(path, body); setRevision(n => n + 1); setEditing(false); }
    catch (cause) { if (cause instanceof ApiError && cause.status === 401) notifySessionExpired(); else setError(errorText(cause)); setRevision(n => n + 1); }
    finally { setBusy(false); }
  }
  return <Card title="Project execution">
    <div className="workspace-link-content">
      <p>Link this saved setup to a project configuration for releases, deployment and task controls.</p>
      {(error || links.error) && <p role="alert">{error || links.error}</p>}
      {!links.data && !links.error && <p role="status">Loading project link…</p>}
      {link && <><p>{link.project_name} · {link.application.name}</p><p>{stateText(link)}</p>
        <p><Link className="cv-btn cv-btn--primary" href={configurationHref(link.application.id)}>Open deployment configuration</Link></p>
        {operator && <div className="project-actions"><button className="cv-btn cv-btn--secondary" disabled={busy || saving || !!links.error} onClick={() => setEditing(value => !value)}>{editing ? "Close linking form" : "Review or change link"}</button>
          <button className="cv-btn cv-btn--secondary" disabled={busy || saving || !!links.error} onClick={() => void mutate(`workspace-configuration-links/${link.id}/remove`, {})}>Unlink saved specification</button></div>}
      </>}
      {operator && links.data && !links.error && (!link || editing) && <LinkForm key={link?.id ?? "new"} initialProject={link?.application.project_id} initialApplication={link?.application.id} busy={busy || saving}
        onSave={applicationId => mutate("workspace-configuration-links", { configuration_id: configurationId, application_id: applicationId, document_revision: documentRevision, expected_link_id: link?.id ?? null })} />}
      {!operator && links.data?.length === 0 && <p>An operator can link this specification to an executable project configuration.</p>}
    </div>
  </Card>;
}

function LinkForm({ initialProject, initialApplication, busy, onSave }: { initialProject?: string; initialApplication?: string; busy: boolean; onSave: (id: string) => Promise<void> }) {
  const projects = useProjectResource<Project[]>("projects");
  const [selectedProject, setSelectedProject] = useState(initialProject ?? "");
  const [selectedApplication, setSelectedApplication] = useState(initialApplication ?? "");
  const projectId = selectedProject || projects.data?.[0]?.id;
  const applications = useProjectResource<Application[]>(projectId ? `applications?project_id=${encodeURIComponent(projectId)}` : null);
  const applicationId = applications.data?.some(app => app.id === selectedApplication) ? selectedApplication : "";
  return <form className="workspace-link-form" onSubmit={event => { event.preventDefault(); if (applicationId) void onSave(applicationId); }}>
    {projects.error && <p role="alert">{projects.error}</p>}{applications.error && <p role="alert">{applications.error}</p>}
    {projects.data?.length === 0 && <p><Link className="cv-link" href="/app/projects">Create a project</Link> before linking this specification.</p>}
    {projectId && <><label className="cv-field">Execution project<select className="cv-input" disabled={busy} value={projectId} onChange={event => { setSelectedProject(event.target.value); setSelectedApplication(""); }}>{projects.data?.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
      <label className="cv-field">Executable configuration<select className="cv-input" required disabled={busy || !applications.data || !!applications.error} value={applicationId} onChange={event => setSelectedApplication(event.target.value)}><option value="">Choose a configuration</option>{applications.data?.map(app => <option key={app.id} value={app.id}>{app.name}</option>)}</select></label>
      <p><Link className="cv-link" href={newConfigurationHref(projectId)}>Create a runnable configuration in this project</Link>, then return here to link it.</p>
      <p className="cv-muted">Linking preserves the saved setup and results. Configure models, verify compatibility and deploy from the project configuration.</p>
      <button className="cv-btn cv-btn--primary" disabled={busy || !applicationId || !!applications.error || !!projects.error}>{busy ? "Saving…" : "Link configuration"}</button>
    </>}
  </form>;
}

export function LinkedWorkspaceSpecifications({ applicationId }: { applicationId: string }) {
  const links = useProjectResource<WorkspaceLink[]>(`workspace-configuration-links?application_id=${encodeURIComponent(applicationId)}`);
  if (links.error) return <p role="alert">Saved specification links: {links.error}</p>;
  if (!links.data?.length) return null;
  return <section aria-label="Linked saved specifications"><h2>Saved specifications</h2><p>These associations preserve earlier setup details and results. Executable releases are managed separately.</p>
    {links.data.map(link => <article className="cv-card" key={link.id}><div><h3>{link.source_name}</h3><p>{stateText(link)}</p>
      {link.source_state !== "missing" && <Link className="cv-link" href={`/app/configurations/${encodeURIComponent(link.configuration_id)}`}>Open saved specification and results</Link>}
    </div></article>)}
  </section>;
}
