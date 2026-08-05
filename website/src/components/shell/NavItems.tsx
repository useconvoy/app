"use client";

import type { Role } from "@/lib/permissions";
import { navForRole } from "./nav";
import { NavLink } from "./NavLink";

/**
 * Pure over `role`: the layout stays thin and passes the membership role in,
 * so the nav is testable without a session. Hiding items here is the lens
 * convenience only; pages enforce their own server-side checks.
 */
export function NavItems({ role }: { role: Role }) {
  return (
    <nav aria-label="Main" className="flex flex-col gap-6">
      {navForRole(role).map((group, index) => (
        <div key={group.label ?? `group-${index}`}>
          {group.label ? (
            <p className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted">
              {group.label}
            </p>
          ) : null}
          <ul aria-label={group.label} className="flex flex-col gap-0.5">
            {group.items.map((item) => (
              <li key={item.href}>
                <NavLink href={item.href} label={item.label} />
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  );
}
