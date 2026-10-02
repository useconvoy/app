"use client";

import Link from "next/link";
import { useState } from "react";
import type { Application, Project } from "@/lib/platform/client";
import { useProjectResource } from "@/lib/projects/client";
import { useSession } from "@/components/configurations/Session";

export const configurationHref = (id: string) => `/app/configurations/${encodeURIComponent(id)}?source=project`;
export const newConfigurationHref = (projectId: string, profileId?: string | null) => `/app/configurations/new?project_id=${encodeURIComponent(projectId)}${profileId ? `&profile_id=${encodeURIComponent(profileId)}` : ""}`;

export function ProjectConfigurations({ projectId }: { projectId: string }) {
  const { operator } = useSession();
  const read = useProjectResource<Application[]>(`applications?project_id=${projectId}`);
  return <section aria-label="Project configurations">
    <p>Configuration releases pin a robot profile, policy and task. Saving a release does not deploy it.</p>
    {operator && <p><Link className="cv-btn cv-btn--primary" href={newConfigurationHref(projectId)}>Create runnable configuration</Link></p>}
    {read.error && <p role="alert">{read.error}</p>}
    {!read.data && !read.error && <p role="status">Loading configurations…</p>}
    {read.data?.length === 0 && <p>No executable configurations in this project yet.</p>}
    <div className="cv-grid">{read.data?.map(app => <Link className="cv-config" key={app.id} href={configurationHref(app.id)}><h2>{app.name}</h2><p>View releases and deployment status →</p></Link>)}</div>
  </section>;
}

export function ProjectConfigurationPicker() {
  const projects = useProjectResource<Project[]>("projects");
  const [chosen, setChosen] = useState("");
  const projectId = chosen || projects.data?.[0]?.id;
  return <section aria-label="Executable configurations">
    <h2>Project configurations</h2>
    {projects.error && <p role="alert">{projects.error}</p>}
    {projects.data?.length === 0 && <p><Link className="cv-link" href="/app/projects">Create a project</Link> to configure a registered robot.</p>}
    {projectId && <><label className="cv-field">Project<select className="cv-input" value={projectId} onChange={e => setChosen(e.target.value)}>{projects.data?.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label><ProjectConfigurations key={projectId} projectId={projectId} /></>}
  </section>;
}
