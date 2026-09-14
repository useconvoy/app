import type { Metadata } from "next";
import { Portal } from "@/components/portal/Portal";
import "@/styles/portal.css";

export const metadata: Metadata = {
  title: { absolute: "Convoy | Jetson demo" },
  description: "Chat with a model on a physical Jetson and inspect its measured telemetry, usage, and traces.",
  alternates: { canonical: "/portal" },
  robots: { index: false, follow: false, googleBot: { index: false, follow: false } },
};

export default function PortalPage() { return <Portal />; }
