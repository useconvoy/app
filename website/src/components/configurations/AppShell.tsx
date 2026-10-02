"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { useWorkspace } from "@/lib/configurations/client";
import { routes } from "@/lib/configurations/routes";
import { AccountMenu } from "./AccountMenu";
import { ConvoyMark } from "./Icons";
import { useSession } from "./Session";

export interface Crumb { label: string; href?: string | null }
export type Area = "configurations" | "projects";

const AREAS: ReadonlyArray<{ id: Area; label: string; href: string }> = [
  { id: "configurations", label: "Configurations", href: routes.index() },
  { id: "projects", label: "Projects", href: "/app/projects" },
];

/** Breadcrumbs; the last crumb is the current page. */
export function Breadcrumbs({ crumbs }: { crumbs: readonly Crumb[] }) {
  return <nav className="cv-crumbs" aria-label="Breadcrumb"><ol>
    {crumbs.map((crumb, i) => <li key={`${i}-${crumb.label}`}>{i < crumbs.length - 1 && crumb.href
      ? <Link href={crumb.href}>{crumb.label}</Link>
      : <span aria-current={i === crumbs.length - 1 ? "page" : undefined}>{crumb.label}</span>}</li>)}
  </ol></nav>;
}

/**
 * The page frame: a slim top bar (wordmark, breadcrumbs, the two areas, the account menu)
 * and `main#main`. No sidebar: a page names its own trail. `area` defaults from the URL;
 * a project page under a Configurations URL (a project configuration) passes "projects".
 */
export function AppShell({ crumbs, children, area }: { crumbs: readonly Crumb[]; children: ReactNode; area?: Area }) {
  const session = useSession();
  const ws = useWorkspace();
  const pathname = usePathname();
  const current: Area = area ?? (pathname.startsWith("/app/projects") ? "projects" : "configurations");
  const sample = current === "configurations" && ws.status === "ready" && ws.source === "sample";
  return <div className="cv-app">
    <a className="cv-skip" href="#main">Skip to content</a>
    <header className="cv-bar">
      <div className="cv-bar__in">
        <Link className="cv-brand" href={routes.index()}><ConvoyMark /><span className="cv-brand__name">Convoy</span></Link>
        <Breadcrumbs crumbs={crumbs} />
        {sample && <span className="cv-sample" title="Sample data: no workspace is saved for this account yet">Sample</span>}
        <nav className="cv-areas" aria-label="Workspace">
          {AREAS.map(item => <Link key={item.id} href={item.href} aria-current={item.id !== current ? undefined : pathname.replace(/\/$/, "") === item.href ? "page" : "true"}>{item.label}</Link>)}
        </nav>
        <AccountMenu />
      </div>
    </header>
    <main id="main" className="cv-main" tabIndex={-1}>
      {session.signOutError && <p className="cv-notice cv-notice--error" role="alert">{session.signOutError}</p>}
      {children}
    </main>
  </div>;
}

/** Page title with its badges, and the page's actions on the right (one primary at most). */
export function PageHeader({ title, badges, actions }: { title: ReactNode; badges?: ReactNode; actions?: ReactNode }) {
  return <div className="cv-head">
    <div className="cv-head__title"><h1>{title}</h1>{badges}</div>
    {actions && <div className="cv-head__actions">{actions}</div>}
  </div>;
}
