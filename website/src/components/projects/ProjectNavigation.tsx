"use client";

import Link from "next/link";
import { useEffect, useRef } from "react";
import type { Crumb } from "@/components/configurations/AppShell";

export const sections = ["Overview", "Robots", "Profiles", "Fleets", "Configurations", "Simulations", "Runs"] as const;
export type Section = typeof sections[number];
export const projectHref = (id: string, section = "Overview") => `/app/projects/${encodeURIComponent(id)}${section === "Overview" ? "" : `?section=${section.toLowerCase()}`}`;
export const robotHref = (projectId: string, robotId: string) => `/app/projects/${encodeURIComponent(projectId)}/robots/${encodeURIComponent(robotId)}`;
/** The controlled joint-position reference runtime (no learned model). */
export const REFERENCE_RUNTIME = "convoy-joint-target-reference-v1";
/** "MuJoCo", "Isaac Sim", or the engine id as reported. */
export const engineLabel = (engine?: string | null) => engine === "isaac" ? "Isaac Sim" : engine === "mujoco" ? "MuJoCo" : engine ?? null;

/** The trail for a project page: Projects / project / …rest. */
export function projectCrumbs(project: { id: string; name?: string } | null, ...rest: Crumb[]): Crumb[] {
  return [{ label: "Projects", href: "/app/projects" }, ...(project ? [{ label: project.name ?? "Project", href: projectHref(project.id) }] : []), ...rest];
}

/** A project's sections as tabs under its title (links, so each section has its own URL). On a phone they scroll; the current one stays in view. */
export function ProjectTabs({ projectId, selected, counts = {} }: { projectId: string; selected: Section; counts?: Partial<Record<Section, number | undefined>> }) {
  const nav = useRef<HTMLElement>(null);
  useEffect(() => {
    const list = nav.current, active = list?.querySelector<HTMLElement>("[aria-current='page']");
    if (!list || !active) return;
    const tab = active.getBoundingClientRect(), box = list.getBoundingClientRect();
    if (tab.right > box.right || tab.left < box.left) list.scrollLeft += tab.left - box.left - 16;
  }, [selected]);
  return <nav ref={nav} className="cv-tabs cv-tabs--links" aria-label="Project sections">
    {sections.map(section => <Link key={section} href={projectHref(projectId, section)} aria-current={selected === section ? "page" : undefined}>
      {section}{counts[section] !== undefined && <span className="cv-tabs__count" aria-hidden="true">{counts[section]!.toLocaleString("en-US")}</span>}
    </Link>)}
  </nav>;
}
