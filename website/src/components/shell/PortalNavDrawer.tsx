"use client";

import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";

/**
 * The console's small-screen navigation. Below md the fixed left rail is
 * hidden and this drawer stands in: a menu button in the top bar opens a
 * panel over the content, carrying the same nav and organization switcher
 * the rail holds on desktop.
 *
 * Escape closes the panel and returns focus to the button, focus moves
 * into it on open, a tap on the scrim closes it, and navigating away
 * closes it too. The button owns the panel through aria-controls, the
 * same discipline as the marketing mobile nav and the account menu.
 */
export function PortalNavDrawer({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const pathname = usePathname();
  const buttonRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  // Navigation is the drawer's job done: the destination page takes over.
  // State adjusted during render, per the React docs, not in an effect.
  const [seenPathname, setSeenPathname] = useState(pathname);
  if (seenPathname !== pathname) {
    setSeenPathname(pathname);
    setOpen(false);
  }

  useEffect(() => {
    if (!open) return;

    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    panelRef.current?.querySelector<HTMLElement>("a, button")?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        aria-expanded={open}
        aria-controls="portal-nav-drawer"
        aria-label={open ? "Close menu" : "Open menu"}
        onClick={() => setOpen((value) => !value)}
        className="flex h-11 w-11 shrink-0 items-center justify-center rounded-md border border-line md:hidden"
      >
        <svg width="18" height="14" viewBox="0 0 18 14" fill="none" aria-hidden="true">
          {open ? (
            <path
              d="M2 2l14 10M16 2L2 12"
              className="stroke-ink"
              strokeWidth="2"
              strokeLinecap="round"
            />
          ) : (
            <path
              d="M1 1h16M1 7h16M1 13h16"
              className="stroke-ink"
              strokeWidth="2"
              strokeLinecap="round"
            />
          )}
        </svg>
      </button>

      {open ? (
        <div className="fixed inset-0 z-50 md:hidden">
          <div
            aria-hidden="true"
            onClick={() => setOpen(false)}
            className="absolute inset-0 bg-graphite/40"
          />
          <div
            id="portal-nav-drawer"
            ref={panelRef}
            onClick={(event) => {
              // A tap on any link inside is navigation; close right away
              // rather than waiting for the route change to land.
              if ((event.target as HTMLElement).closest("a")) setOpen(false);
            }}
            className="absolute inset-y-0 left-0 flex w-72 max-w-[85vw] flex-col gap-6 overflow-y-auto border-r border-line bg-card px-3 py-6 shadow-lg"
          >
            {children}
          </div>
        </div>
      ) : null}
    </>
  );
}
