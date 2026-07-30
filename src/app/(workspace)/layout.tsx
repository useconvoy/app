import { AppShell } from "@/components/AppShell";

// Only the workspace routes get the client-side application shell — the sidebar,
// the mobile drawer, and the account fetch. Keeping it in this route group means
// the public marketing routes ship none of that JavaScript, and it replaces the
// path-prefix check the shell used to do for itself.
export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  return <AppShell>{children}</AppShell>;
}
