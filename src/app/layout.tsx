import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Convoy — Company agents, governed",
  description: "Where company agents are hired, badged, and put to work.",
};

const NAV = [
  { href: "/", label: "Fleet", icon: "▦" },
  { href: "/environments", label: "Environments", icon: "⬡" },
  { href: "/agents", label: "Agents", icon: "🤖" },
  { href: "/runs", label: "Runs", icon: "▶" },
  { href: "/approvals", label: "Approvals", icon: "✋" },
  { href: "/audit", label: "Audit log", icon: "☰" },
  { href: "/systems", label: "External systems", icon: "🔌" },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="shell">
          <aside className="sidebar">
            <Link href="/" className="brand">
              <span className="brand-mark">◆</span> Convoy
            </Link>
            <div className="workspace">Meridian Labs · RevOps</div>
            <nav>
              {NAV.map((n) => (
                <Link key={n.href} href={n.href} className="nav-link">
                  <span className="nav-icon">{n.icon}</span> {n.label}
                </Link>
              ))}
            </nav>
            <div className="sidebar-foot">
              <div className="pill pill-dim">demo mode · deterministic model</div>
            </div>
          </aside>
          <main className="main">{children}</main>
        </div>
      </body>
    </html>
  );
}
