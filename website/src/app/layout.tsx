import type { Metadata } from "next";
import { Besley, Public_Sans, Spline_Sans_Mono } from "next/font/google";

import "@/styles/globals.css";

const besley = Besley({ subsets: ["latin"], variable: "--font-besley", weight: ["400", "500", "600", "700"] });
const publicSans = Public_Sans({ subsets: ["latin"], variable: "--font-public-sans", weight: ["400", "500", "600", "700"] });
const splineMono = Spline_Sans_Mono({ subsets: ["latin"], variable: "--font-spline-mono", weight: ["400", "500", "600"] });

export const metadata: Metadata = {
  title: { default: "Convoy", template: "%s · Convoy" },
  description: "Routine work, done carefully. Convoy runs your routines and holds for your judgment.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${besley.variable} ${publicSans.variable} ${splineMono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
