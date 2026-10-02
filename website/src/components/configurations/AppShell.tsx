"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useWorkspace } from "@/lib/configurations/client";
import { routes } from "@/lib/configurations/routes";
import { ConvoyMark } from "./Icons";
import { useSession } from "./Session";

export interface Crumb { label: string; href?: string | null }

/** Breadcrumbs; the last crumb is the current page. */
export function Breadcrumbs({ crumbs }: { crumbs: readonly Crumb[] }) {
  return <nav className="cv-crumbs" aria-label="Breadcrumb"><ol>
    {crumbs.map((crumb, i) => <li key={`${i}-${crumb.label}`}>{i < crumbs.length - 1 && crumb.href
      ? <Link href={crumb.href}>{crumb.label}</Link>
      : <span aria-current={i === crumbs.length - 1 ? "page" : undefined}>{crumb.label}</span>}</li>)}
  </ol></nav>;
}

/**
 * The page frame: a slim top bar (wordmark, breadcrumbs, account, sign out) and
 * `main#main`. Configurations is the only area, so there is no other navigation.
 */
export function AppShell({ crumbs, children }: { crumbs: readonly Crumb[]; children: ReactNode }) {
  const session = useSession();
  const ws = useWorkspace();
  const sample = ws.status === "ready" && ws.source === "sample";
  return <div className="cv-app">
    <a className="cv-skip" href="#main">Skip to content</a>
    <header className="cv-bar">
      <div className="cv-bar__in">
        <Link className="cv-brand" href={routes.index()}><ConvoyMark />Convoy</Link>
        <Breadcrumbs crumbs={crumbs} />
        <Link className="cv-link" href="/app/projects">Projects</Link>
        {sample && <span className="cv-sample" title="Sample data: no workspace is saved for this account yet">Sample</span>}
        <div className="cv-account">
          <span className="cv-account__email">{session.email}</span>
          <button className="cv-link" type="button" disabled={session.signingOut} onClick={() => void session.signOut()}>{session.signingOut ? "Signing out…" : "Sign out"}</button>
        </div>
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
