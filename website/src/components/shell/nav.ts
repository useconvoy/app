/**
 * The portal nav map (DESIGN §5 shell): Overview · Checkpoints · Runs,
 * BUILD (Routines · Workspaces), IMPROVE (Routine evaluation · Learning),
 * then Logs · Admin. Filtering by role is a lens only; every page enforces
 * its own server-side check.
 */
import { visibleAreas, type Role } from "@/lib/permissions";

export interface NavItem {
  /** Area key matched against `visibleAreas(role)`. */
  area: string;
  label: string;
  href: string;
}

export interface NavGroup {
  /** Absent for the ungrouped top and bottom items. */
  label?: string;
  items: NavItem[];
}

export const NAV_GROUPS: NavGroup[] = [
  {
    items: [
      { area: "overview", label: "Overview", href: "/app" },
      { area: "checkpoints", label: "Checkpoints", href: "/app/checkpoints" },
      { area: "runs", label: "Runs", href: "/app/runs" },
    ],
  },
  {
    label: "Build",
    items: [
      { area: "routines", label: "Routines", href: "/app/routines" },
      { area: "workspaces", label: "Workspaces", href: "/app/workspaces" },
    ],
  },
  {
    label: "Improve",
    items: [
      { area: "evaluation", label: "Routine evaluation", href: "/app/evaluation" },
      { area: "learning", label: "Learning", href: "/app/learning" },
    ],
  },
  {
    items: [
      { area: "logs", label: "Logs", href: "/app/logs" },
      { area: "admin", label: "Admin", href: "/app/admin" },
    ],
  },
];

/** Nav groups a role sees, with empty groups dropped. */
export function navForRole(role: Role): NavGroup[] {
  const areas = visibleAreas(role);
  return NAV_GROUPS.map((group) => ({
    ...group,
    items: group.items.filter((item) => areas.includes(item.area)),
  })).filter((group) => group.items.length > 0);
}

/** Flat route list for the jump palette, in nav order. */
export function routesForRole(role: Role): Array<{ href: string; label: string }> {
  return navForRole(role).flatMap((group) => group.items.map(({ href, label }) => ({ href, label })));
}
