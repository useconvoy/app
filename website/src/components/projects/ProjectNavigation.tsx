"use client";

import Link from "next/link";
import { useProjectResource, type Fleet } from "@/lib/projects/client";
import type { Robot } from "@/lib/platform/client";

export const sections = ["Overview", "Robots", "Profiles", "Fleets", "Configurations", "Simulations", "Runs"] as const;
export const projectHref = (id: string, section = "Overview") => `/app/projects/${encodeURIComponent(id)}?section=${section.toLowerCase()}`;

export function ProjectNavigation({ projectId, selected }: { projectId: string; selected?: string }) {
  const fleets = useProjectResource<Fleet[]>(`fleets?project_id=${projectId}`, 0, true);
  const robots = useProjectResource<Robot[]>(`robots?project_id=${projectId}`, 0, true);
  return <><nav className="project-navigation" aria-label="Project sections">{sections.map(section =>
    <Link key={section} href={projectHref(projectId, section)} aria-current={selected?.toLowerCase() === section.toLowerCase() ? "page" : undefined}>{section}</Link>
  )}</nav>
    <nav className="project-fleet-navigation" aria-label="Fleet robots">
      <p className="project-sidebar__label">Fleets</p>
      {fleets.data?.map(fleet => <details key={fleet.id}>
        <summary><span>{fleet.name}</span><span className="project-nav-count">{fleet.robot_ids.length}</span></summary>
        {fleet.robot_ids.map(id => <Link key={id} href={`/app/projects/${projectId}/robots/${id}`}>{robots.data?.find(robot => robot.id === id)?.name ?? "Robot"}</Link>)}
        {fleet.robot_ids.length === 0 && <p className="project-sidebar__empty">No robots assigned</p>}
      </details>)}
      {fleets.data?.length === 0 && <Link className="project-sidebar__empty" href={projectHref(projectId, "Fleets")}>Create a fleet →</Link>}
      {fleets.error && <p className="project-sidebar__empty">Fleet list unavailable</p>}
    </nav>
  </>;
}
