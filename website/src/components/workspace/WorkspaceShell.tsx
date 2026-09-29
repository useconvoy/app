"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/** Shared navigation; each tool retains its existing, scoped authentication. */
export function WorkspaceShell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const device = path.startsWith("/portal") || path.startsWith("/app/device");
  return <div className="workspace-shell">
    <header className="workspace-bar">
      <Link href="/app" className="workspace-brand">Convoy <span>Workspace</span></Link>
      <nav aria-label="Workspace">
        <Link href="/app/applications" aria-current={!device ? "page" : undefined}>Robot applications</Link>
        <Link href="/app/device" aria-current={device ? "page" : undefined}>Device & inference</Link>
      </nav>
      <Link className="workspace-website" href="/">Website ↗</Link>
    </header>
    {children}
  </div>;
}
