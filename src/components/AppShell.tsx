"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { SideNav } from "./SideNav";

function Brand() {
  return (
    <Link href="/" className="brand">
      <span className="brand-mark">C</span> Convoy Labs
    </Link>
  );
}

function Persona() {
  return (
    <div className="sidebar-foot">
      <span className="avatar">MT</span>
      <div className="who">
        <b>Maya Torres</b>
        <span>RevOps Lead · Approver</span>
      </div>
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const pathname = usePathname();

  // Close the drawer on navigation.
  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  // Prevent background scroll while the drawer is open.
  useEffect(() => {
    document.body.style.overflow = open ? "hidden" : "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [open]);

  return (
    <div className="shell">
      <header className="topbar">
        <button
          className="menu-btn"
          aria-label={open ? "Close navigation" : "Open navigation"}
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
            {open ? <path d="m6 6 12 12M18 6 6 18" /> : <path d="M4 6h16M4 12h16M4 18h16" />}
          </svg>
        </button>
        <Brand />
      </header>

      {open && <div className="drawer-backdrop" onClick={() => setOpen(false)} />}

      <aside className={`sidebar${open ? " open" : ""}`}>
        <Brand />
        <div className="workspace">Meridian Labs · RevOps workspace</div>
        <SideNav />
        <Persona />
      </aside>

      <main className="main">{children}</main>
    </div>
  );
}
