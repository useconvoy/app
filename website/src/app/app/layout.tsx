import type { Metadata } from "next";
import { LiveDeviceProvider } from "@/components/configurations/LiveDeviceProvider";
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
/** One live-device poller for the whole workspace; it polls only while a page shows a device-bound robot. */
export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  return <LiveDeviceProvider>{children}</LiveDeviceProvider>;
}
