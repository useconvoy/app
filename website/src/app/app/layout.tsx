import type { Metadata } from "next";
import "@/styles/workspace.css";
import "@/styles/console.css";
import "@/styles/portal.css";
// After console.css: the replay player is shared with the legacy console page.
import "@/styles/configurations.css";

export const metadata: Metadata = {
  title: { absolute: "Convoy | Workspace" },
  description: "Configurations, robots and evals for physical AI.",
  robots: { index: false, follow: false, googleBot: { index: false, follow: false } },
};
/**
 * The workspace styles. The Configurations live-device poller and platform reads are
 * mounted inside its session gate (src/components/configurations/Session.tsx), so
 * they end with the session.
 */
export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
