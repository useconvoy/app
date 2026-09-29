import type { Metadata } from "next";
import { WorkspaceShell } from "@/components/workspace/WorkspaceShell";
import "@/styles/workspace.css";
import "@/styles/console.css";
import "@/styles/portal.css";

export const metadata: Metadata = {
  title: { absolute: "Convoy | Workspace" },
  description: "Deploy robot applications, run simulations, and observe device inference.",
  robots: { index: false, follow: false, googleBot: { index: false, follow: false } },
};
export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  return <WorkspaceShell>{children}</WorkspaceShell>;
}
