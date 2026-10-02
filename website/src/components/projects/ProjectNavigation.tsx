"use client";

import Link from "next/link";

export const sections = ["Overview", "Robots", "Profiles", "Fleets", "Configurations", "Simulations", "Runs"] as const;
export const projectHref = (id: string, section = "Overview") => `/app/projects/${encodeURIComponent(id)}?section=${section.toLowerCase()}`;

export function ProjectNavigation({ projectId, selected }: { projectId: string; selected?: string }) {
  return <nav className="cv-tabs project-navigation" aria-label="Project sections">{sections.map(section =>
    <Link key={section} href={projectHref(projectId, section)} aria-current={selected?.toLowerCase() === section.toLowerCase() ? "page" : undefined}>{section}</Link>
  )}</nav>;
}
