"use client";

import Link from "next/link";
import { Icon } from "@/components/configurations/Icons";
import { useRunnableConfigurations } from "@/lib/projects/client";
import { projectHref } from "./ProjectNavigation";

export const configurationHref = (id: string) => `/app/configurations/${encodeURIComponent(id)}?source=project`;
export const newConfigurationHref = (projectId: string, profileId?: string | null) => `/app/configurations/new?project_id=${encodeURIComponent(projectId)}${profileId ? `&profile_id=${encodeURIComponent(profileId)}` : ""}`;

/**
 * One row under the Configurations cards when the account's projects have runnable
 * configurations: how many, and a way to them. Nothing otherwise.
 */
export function ProjectConfigurationsRow() {
  const { items } = useRunnableConfigurations();
  if (!items?.length) return null;
  const single = new Set(items.map(item => item.project.id)).size === 1;
  const href = items.length === 1 ? configurationHref(items[0].application.id) : single ? projectHref(items[0].project.id, "Configurations") : "/app/projects";
  return <Link className="cv-strip" href={href}>
    <span>{items.length === 1 ? "1 project configuration" : `${items.length} project configurations`}</span>
    <span className="cv-strip__action">View<Icon name="chevron-right" small /></span>
  </Link>;
}
