import type { Metadata, Viewport } from "next";
import { AppShell } from "@/components/AppShell";
import "./globals.css";

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL ?? "https://shark-app-dj8b4.ondigitalocean.app";

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: {
    default: "Convoy Labs — The control plane for company agents",
    template: "%s · Convoy Labs",
  },
  description:
    "Create, permission, test, promote, and supervise company-owned agents for routine work—without handing away human judgment.",
  applicationName: "Convoy Labs",
  authors: [{ name: "Convoy Labs" }],
  creator: "Convoy Labs",
  publisher: "Convoy Labs",
  formatDetection: { email: false, address: false, telephone: false },
  openGraph: {
    type: "website",
    siteName: "Convoy Labs",
    title: "Convoy Labs — The control plane for company agents",
    description:
      "Create, permission, test, promote, and supervise company-owned agents from Sandbox to Production.",
    url: "/",
    images: [{ url: "/og.png", width: 1200, height: 630, alt: "Convoy Labs policy gateway preview" }],
  },
  twitter: {
    card: "summary_large_image",
    title: "Convoy Labs — The control plane for company agents",
    description:
      "Create, permission, test, promote, and supervise company-owned agents from Sandbox to Production.",
    images: ["/og.png"],
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

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
