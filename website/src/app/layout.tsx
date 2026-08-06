import type { Metadata, Viewport } from "next";
import { Besley, Public_Sans, Spline_Sans_Mono } from "next/font/google";

import { color } from "@/lib/tokens";
import "@/styles/globals.css";

/* Only the weights the design actually uses. Besley carries the display line
 * at 500, its italic second line, and the wordmark at 800; nothing renders it
 * at 400, 600, or 700. */
const besley = Besley({
  subsets: ["latin"],
  variable: "--font-besley",
  weight: ["500", "800"],
  style: ["normal", "italic"],
  display: "swap",
});

const publicSans = Public_Sans({
  subsets: ["latin"],
  variable: "--font-public-sans",
  weight: ["400", "500", "600", "700"],
  display: "swap",
});

const splineMono = Spline_Sans_Mono({
  subsets: ["latin"],
  variable: "--font-spline-mono",
  weight: ["400", "500", "600"],
  display: "swap",
});

/* "Convoy" alone belongs to an open-source webhooks gateway and a freight
 * network, both of which outrank this site for the bare word. Every place a
 * machine reads the name, it reads Convoy Labs. */
export const metadata: Metadata = {
  metadataBase: new URL("https://deployconvoy.com"),
  title: {
    default: "Convoy Labs: routine work, done carefully",
    template: "%s · Convoy Labs",
  },
  description:
    "Convoy runs the routines your team repeats every close and every quarter. Each one rehearses safely first, holds for your sign-off, and leaves a record built for an auditor.",
  applicationName: "Convoy Labs",
  alternates: { canonical: "/" },
  openGraph: {
    siteName: "Convoy Labs",
    type: "website",
    locale: "en_US",
    url: "/",
  },
  twitter: { card: "summary_large_image" },
  robots: { index: true, follow: true },
};

export const viewport: Viewport = {
  themeColor: color.field,
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="en"
      className={`${besley.variable} ${publicSans.variable} ${splineMono.variable}`}
    >
      <body>{children}</body>
    </html>
  );
}
