import type { Metadata } from "next";
import { ConfigurationsRoot } from "@/components/configurations/Session";

export const metadata: Metadata = { title: { absolute: "Convoy | Configurations" } };

/** Client-side session gate and the workspace document, shared by every Configurations page. */
export default function ConfigurationsLayout({ children }: { children: React.ReactNode }) {
  return <ConfigurationsRoot>{children}</ConfigurationsRoot>;
}
