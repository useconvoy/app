import Link from "next/link";

/** One entry point. Application and device tools share the same session. */
export function WorkspaceShell({ children }: { children: React.ReactNode }) {
  return <div className="workspace-shell">
    <header className="workspace-bar">
      <Link href="/app" className="workspace-brand">Convoy <span>Workspace</span></Link>
      <Link className="workspace-website" href="/">Website ↗</Link>
    </header>
    {children}
  </div>;
}
