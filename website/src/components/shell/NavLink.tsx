"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/**
 * A left-nav link that knows when it is the active area. Overview owns
 * exactly `/app`; every other item owns its subtree.
 */
export function NavLink({ href, label }: { href: string; label: string }) {
  const pathname = usePathname() ?? "";
  const active = href === "/app" ? pathname === "/app" : pathname === href || pathname.startsWith(`${href}/`);
  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={`block rounded-md px-3 py-2 text-sm font-medium transition-colors ${
        active ? "bg-pine text-card" : "text-muted hover:bg-field hover:text-ink"
      }`}
    >
      {label}
    </Link>
  );
}
