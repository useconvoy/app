import type { Metadata } from "next";
import { WorkspaceRoot } from "@/components/configurations/Session";
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
 * The workspace styles, and one session for Configurations and Projects: their session
 * gate, workspace document, live-device poller and platform reads are mounted once
 * (src/components/configurations/Session.tsx), so they end with the session and survive
 * moving between the two areas.
 */
export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  return <WorkspaceRoot>{children}</WorkspaceRoot>;
}
