import type { Metadata } from "next";
import "@/styles/workspace.css";
import "@/styles/console.css";
import "@/styles/portal.css";
import "@/styles/configurations.css";
import "@/styles/configurations-index.css";
import "@/styles/configurations-dashboard.css";
import "@/styles/configurations-robot.css";
import "@/styles/configurations-eval.css";

export const metadata: Metadata = {
  title: { absolute: "Convoy | Workspace" },
  description: "Deploy robot applications, run simulations, and observe device inference.",
  robots: { index: false, follow: false, googleBot: { index: false, follow: false } },
};
/**
 * The workspace styles. The Configurations live-device poller is mounted inside its
 * session gate (src/components/configurations/Session.tsx), so it ends with the session.
 */
export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
