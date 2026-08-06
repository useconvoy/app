"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

/**
 * The small-screen navigation.
 *
 * Below 780px the desktop links are hidden, and until now nothing replaced
 * them: the site simply had no navigation on a phone. This is the second and
 * last client component on the marketing pages.
 *
 * Escape closes the panel, focus moves into it on open and returns to the
 * button on close, and the button owns the panel through aria-controls so a
 * screen reader is told what it expands.
 */
export function MobileNav({
  items,
}: {
  items: readonly { href: string; label: string }[];
}) {
  const [open, setOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;

    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    panelRef.current?.querySelector("a")?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        aria-expanded={open}
        aria-controls="mobile-nav-panel"
        aria-label={open ? "Close menu" : "Open menu"}
        onClick={() => setOpen((value) => !value)}
        className="inline-flex items-center justify-center rounded-lg border border-line p-2.5 md:hidden"
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
        <div
          id="mobile-nav-panel"
          ref={panelRef}
          className="absolute inset-x-0 top-full flex flex-col border-b border-line bg-card px-5 pt-2 pb-4 shadow-[0_24px_48px_-32px_rgba(21,59,46,.3)] md:hidden"
        >
          {items.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              onClick={() => setOpen(false)}
              className="border-b border-line-soft px-0.5 py-3 text-[15px] font-medium text-ink last:border-b-0"
            >
              {item.label}
            </Link>
          ))}
          <Link
            href="/sign-in"
            onClick={() => setOpen(false)}
            className="px-0.5 py-3 text-[15px] font-medium text-muted"
          >
            Sign in
          </Link>
        </div>
      ) : null}
    </>
  );
}
