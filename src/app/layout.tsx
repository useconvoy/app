import type { Metadata, Viewport } from "next";
import "./globals.css";

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL ?? "https://shark-app-dj8b4.ondigitalocean.app";

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: {
    default: "Convoy Labs — The control plane for company agents",
    template: "%s · Convoy Labs",
  },
  description:
    "Create, permission, test, promote and supervise company-owned agents for routine work — without handing away human judgment.",
  applicationName: "Convoy Labs",
  authors: [{ name: "Convoy Labs" }],
  creator: "Convoy Labs",
  publisher: "Convoy Labs",
  formatDetection: { email: false, address: false, telephone: false },
  // Social card images come from `opengraph-image.tsx`, which Next generates at
  // build time from the same tokens as the site.
  openGraph: {
    type: "website",
    siteName: "Convoy Labs",
    locale: "en_US",
    title: "Convoy Labs — The control plane for company agents",
    description:
      "Agents that belong to the company, not to a login. The environment decides what an agent may touch; consequential actions stop for a human.",
    url: "/",
  },
  twitter: {
    card: "summary_large_image",
    title: "Convoy Labs — The control plane for company agents",
    description:
      "Agents that belong to the company, not to a login. The environment decides what an agent may touch; consequential actions stop for a human.",
  },
  robots: {
    index: true,
    follow: true,
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

// The root layout stays a Server Component with no client boundary, so public
// pages ship no application JavaScript. The workspace shell lives in
// `(workspace)/layout.tsx`.
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
