import type { Metadata } from "next";
import { Console } from "@/components/console/Console";
import "@/styles/console.css";

export const metadata: Metadata = {
  title: { absolute: "Convoy | Application console" },
  description: "Connect simulated robots, deploy application releases, and inspect acknowledged mission outcomes.",
  alternates: { canonical: "/console" },
  robots: { index: false, follow: false, googleBot: { index: false, follow: false } },
};

export default function ConsolePage() { return <Console />; }
