import type { Metadata } from "next";
import Link from "next/link";
import { SideNav } from "@/components/SideNav";
import "./globals.css";

export const metadata: Metadata = {
  title: "Convoy — Company agents, governed",
  description: "Where company agents are hired, badged, and put to work.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="shell">
          <aside className="sidebar">
            <Link href="/" className="brand">
              <span className="brand-mark">C</span> Convoy
            </Link>
            <div className="workspace">Meridian Labs · RevOps workspace</div>
            <SideNav />
            <div className="sidebar-foot">
              <span className="avatar">MT</span>
              <div className="who">
                <b>Maya Torres</b>
                <span>RevOps Lead · Approver</span>
              </div>
            </div>
          </aside>
          <main className="main">{children}</main>
        </div>
      </body>
    </html>
  );
}
