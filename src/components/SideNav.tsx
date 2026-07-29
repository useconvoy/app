"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  IconBot,
  IconGrid,
  IconHand,
  IconLayers,
  IconList,
  IconPlay,
  IconPlug,
} from "./icons";

const GROUPS: { label: string; items: { href: string; label: string; icon: React.ReactNode }[] }[] = [
  {
    label: "Operate",
    items: [
      { href: "/", label: "Fleet overview", icon: <IconGrid /> },
      { href: "/runs", label: "Runs", icon: <IconPlay /> },
      { href: "/approvals", label: "Approvals", icon: <IconHand /> },
    ],
  },
  {
    label: "Configure",
    items: [
      { href: "/agents", label: "Agents", icon: <IconBot /> },
      { href: "/environments", label: "Environments", icon: <IconLayers /> },
    ],
  },
  {
    label: "Govern",
    items: [{ href: "/audit", label: "Audit log", icon: <IconList /> }],
  },
  {
    label: "Systems of record",
    items: [{ href: "/systems", label: "Connected systems", icon: <IconPlug /> }],
  },
];

export function SideNav() {
  const pathname = usePathname();
  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(href + "/");
  return (
    <nav aria-label="Primary">
      {GROUPS.map((g) => (
        <div className="nav-group" key={g.label}>
          <div className="nav-group-label">{g.label}</div>
          {g.items.map((n) => (
            <Link key={n.href} href={n.href} className={`nav-link${isActive(n.href) ? " active" : ""}`}>
              {n.icon} {n.label}
            </Link>
          ))}
        </div>
      ))}
    </nav>
  );
}
