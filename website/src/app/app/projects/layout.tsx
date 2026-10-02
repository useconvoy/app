import type { Metadata } from "next";

export const metadata: Metadata = { title: { absolute: "Convoy | Projects" } };

/** The session gate and the workspace document are shared with Configurations (src/app/app/layout.tsx). */
export default function ProjectsLayout({ children }: { children: React.ReactNode }) {
  return children;
}
