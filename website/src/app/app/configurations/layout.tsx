import type { Metadata } from "next";

export const metadata: Metadata = { title: { absolute: "Convoy | Configurations" } };

/** The session gate and the workspace document are shared with Projects (src/app/app/layout.tsx). */
export default function ConfigurationsLayout({ children }: { children: React.ReactNode }) {
  return children;
}
