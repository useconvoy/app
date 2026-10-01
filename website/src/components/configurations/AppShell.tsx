"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useWorkspace } from "@/lib/configurations/client";
import { routes } from "@/lib/configurations/routes";
import { ConvoyMark } from "./Icons";
import { useSession } from "./Session";

export interface Crumb { label: string; href?: string | null }
export type NavKey = "configurations" | "suites" | "devices" | "applications";

const NAV: ReadonlyArray<{ key: NavKey; number: string; label: string; href: string }> = [
  { key: "configurations", number: "01", label: "Configurations", href: routes.index() },
  { key: "suites", number: "02", label: "Eval suites", href: routes.suites() },
  { key: "devices", number: "03", label: "Devices & inference", href: routes.devices() },
  { key: "applications", number: "04", label: "Applications", href: routes.applications() },
];

/** Mono breadcrumbs; the last crumb is the current page. */
export function Breadcrumbs({ crumbs }: { crumbs: readonly Crumb[] }) {
  return <nav className="cfg-crumbs" aria-label="Breadcrumb"><ol>
    {crumbs.map((crumb, i) => <li key={`${i}-${crumb.label}`}>{i < crumbs.length - 1 && crumb.href ? <Link href={crumb.href}>{crumb.label}</Link> : <span aria-current={i === crumbs.length - 1 ? "page" : undefined}>{crumb.label}</span>}</li>)}
  </ol></nav>;
}

/**
 * The Configurations page frame: sidebar (workspace label, numbered nav, footer
 * note), top bar (breadcrumbs, optional context badge, account and sign-out) and
 * `main#main`. Render the page header, notices and sections as children.
 * Overlays (Modal, Drawer) render inside `children` as well.
 */
export function AppShell({ crumbs, context, nav = "configurations", children }: { crumbs: readonly Crumb[]; context?: ReactNode; nav?: NavKey; children: ReactNode }) {
  const session = useSession();
  const ws = useWorkspace();
  const meta = ws.workspace?.meta;
  return <div className="portal-shell cfg-shell">
    <a className="portal-skip" href="#main">Skip to content</a>
    <aside className="portal-sidebar">
      <Link className="convoy-wordmark portal-wordmark" href={routes.index()}><ConvoyMark />Convoy</Link>
      <div className="portal-workspace-label">{meta?.label ?? "Workspace"}</div>
      <nav aria-label="Workspace">
        {NAV.map(item => <Link key={item.key} href={item.href} aria-current={item.key === nav ? "page" : undefined}>
          <span className="portal-nav-number" aria-hidden="true">{item.number}</span>{item.label}<span className="portal-nav-arrow" aria-hidden="true">↗</span>
        </Link>)}
      </nav>
      <div className="portal-sidebar-bottom"><p>{meta?.description ?? "Configurations, robots and evidence."}</p><Link className="text-link" href={routes.website()}>Convoy website ↗</Link></div>
    </aside>
    <div className="portal-workarea">
      <header className="portal-topbar cfg-topbar">
        <div><Breadcrumbs crumbs={crumbs} />{context}</div>
        <div className="cfg-account">
          <span>{session.email}</span>
          <button className="portal-text-button" type="button" disabled={session.signingOut} onClick={() => void session.signOut()}>{session.signingOut ? "Signing out…" : "Sign out"}</button>
        </div>
      </header>
      <main id="main" className="portal-main" tabIndex={-1}>
        {session.signOutError && <div className="portal-notice portal-notice-error cfg-notice" role="alert"><p>{session.signOutError}</p></div>}
        {children}
      </main>
    </div>
  </div>;
}
