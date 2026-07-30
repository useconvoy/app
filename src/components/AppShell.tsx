"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { SideNav } from "./SideNav";

function Brand() {
  return (
    <Link href="/dashboard" className="brand">
      <span className="brand-mark">C</span> Convoy Labs
    </Link>
  );
}

interface AccountContext {
  account: { name: string; email: string };
  workspaces: Array<{ id: string; name: string; role: string }>;
}

function Persona({ context }: { context: AccountContext | null }) {
  const initials = context?.account.name
    .split(/\s+/)
    .map((part) => part[0])
    .join("")
    .slice(0, 2)
    .toUpperCase() ?? "MT";
  const logout = async () => {
    await fetch("/api/auth/logout", { method: "POST" });
    window.location.assign("/login");
  };
  return (
    <div className="sidebar-foot">
      <span className="avatar">{initials}</span>
      <div className="who">
        <b>{context?.account.name ?? "Maya Torres"}</b>
        <span>{context?.account.email ?? "RevOps Lead · Approver"}</span>
      </div>
      {context && (
        <button className="btn btn-ghost btn-sm" onClick={logout}>
          Sign out
        </button>
      )}
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const [accountContext, setAccountContext] =
    useState<AccountContext | null>(null);
  const pathname = usePathname();
  const menuRef = useRef<HTMLButtonElement>(null);
  const sidebarRef = useRef<HTMLElement>(null);
  const wasOpen = useRef(false);

  useEffect(() => {
    fetch("/api/auth/me")
      .then((response) => (response.ok ? response.json() : null))
      .then((result) => {
        if (result?.account) setAccountContext(result);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    const media = window.matchMedia("(max-width: 900px)");
    const sync = () => setIsMobile(media.matches);
    sync();
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, []);

  // Close the drawer on navigation.
  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  // Prevent background scroll while the drawer is open.
  useEffect(() => {
    document.body.style.overflow = open && isMobile ? "hidden" : "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [isMobile, open]);

  useEffect(() => {
    if (!isMobile) return;
    if (open) {
      wasOpen.current = true;
      const sidebar = sidebarRef.current;
      sidebar?.querySelector<HTMLButtonElement>("[data-drawer-close]")?.focus();
      const containDrawerFocus = (event: KeyboardEvent) => {
        if (event.key === "Escape") {
          setOpen(false);
          return;
        }
        if (event.key !== "Tab" || !sidebar) return;
        const focusable = Array.from(
          sidebar.querySelectorAll<HTMLElement>('a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])'),
        );
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      };
      window.addEventListener("keydown", containDrawerFocus);
      return () => window.removeEventListener("keydown", containDrawerFocus);
    }
    if (wasOpen.current) {
      wasOpen.current = false;
      menuRef.current?.focus();
    }
  }, [isMobile, open]);

  return (
    <div className="shell">
      <header className="topbar" aria-hidden={isMobile && open ? true : undefined} inert={isMobile && open ? true : undefined}>
        <button
          ref={menuRef}
          className="menu-btn"
          aria-label={open ? "Close navigation" : "Open navigation"}
          aria-expanded={open}
          aria-controls="workspace-navigation"
          onClick={() => setOpen((v) => !v)}
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
            {open ? <path d="m6 6 12 12M18 6 6 18" /> : <path d="M4 6h16M4 12h16M4 18h16" />}
          </svg>
        </button>
        <Brand />
      </header>

      {open && (
        <div className="drawer-backdrop" aria-hidden="true" onClick={() => setOpen(false)} />
      )}

      <aside
        id="workspace-navigation"
        ref={sidebarRef}
        className={`sidebar${open ? " open" : ""}`}
        aria-hidden={isMobile && !open ? true : undefined}
        inert={isMobile && !open ? true : undefined}
      >
        <div className="sidebar-mobile-head">
          <Brand />
          <button
            type="button"
            className="sidebar-close"
            data-drawer-close
            aria-label="Close navigation"
            onClick={() => setOpen(false)}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
              <path d="m6 6 12 12M18 6 6 18" />
            </svg>
          </button>
        </div>
        <div className="workspace">
          {accountContext?.workspaces[0]?.name ??
            "Meridian Labs · RevOps workspace"}
        </div>
        <SideNav />
        <Persona context={accountContext} />
      </aside>

      <main
        className="main"
        id="workspace-main"
        aria-hidden={isMobile && open ? true : undefined}
        inert={isMobile && open ? true : undefined}
      >
        <div className="demo-banner" role="note">
          <strong>Fictional demo workspace</strong>
          <span>External systems, credentials, people, companies, and activity are simulated.</span>
        </div>
        {children}
      </main>
    </div>
  );
}
