import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";

import { SITE } from "@/content/homepage";
import { color } from "@/lib/tokens";
import "@/styles/globals.css";

/* IBM Plex, self-hosted under the SIL Open Font License (see
 * src/assets/fonts/LICENSE-IBM-Plex.txt). Only the weights the type scale
 * uses: Sans at 400, 500, 600 plus the 400 italic; Mono at 400 and 500. */
const plexSans = localFont({
  src: [
    { path: "../assets/fonts/ibm-plex-sans-latin-400-normal.woff2", weight: "400", style: "normal" },
    { path: "../assets/fonts/ibm-plex-sans-latin-400-italic.woff2", weight: "400", style: "italic" },
    { path: "../assets/fonts/ibm-plex-sans-latin-500-normal.woff2", weight: "500", style: "normal" },
    { path: "../assets/fonts/ibm-plex-sans-latin-600-normal.woff2", weight: "600", style: "normal" },
  ],
  variable: "--font-plex-sans",
  display: "swap",
  fallback: ["system-ui", "Segoe UI", "Helvetica Neue", "Arial", "sans-serif"],
});

const plexMono = localFont({
  src: [
    { path: "../assets/fonts/ibm-plex-mono-latin-400-normal.woff2", weight: "400", style: "normal" },
    { path: "../assets/fonts/ibm-plex-mono-latin-500-normal.woff2", weight: "500", style: "normal" },
  ],
  variable: "--font-plex-mono",
  display: "swap",
  fallback: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
});

export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? SITE.url),
  title: { absolute: SITE.title },
  description: SITE.description,
  applicationName: SITE.name,
  alternates: { canonical: "/" },
  openGraph: {
    title: SITE.ogTitle,
    description: SITE.ogDescription,
    siteName: SITE.name,
    type: "website",
    locale: "en_US",
    url: "/",
  },
  twitter: {
    card: "summary_large_image",
    title: SITE.ogTitle,
    description: SITE.ogDescription,
  },
  robots: { index: true, follow: true },
};

export const viewport: Viewport = {
  themeColor: color.background,
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${plexSans.variable} ${plexMono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
