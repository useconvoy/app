"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { accountCopy } from "@/lexicon";

/**
 * Top-bar account menu: an initials button opening the personal surfaces
 * (profile, organization, my runs, settings) and sign out. Escape closes
 * and returns focus to the button, focus moves into the menu on open, and
 * the button owns the menu through aria-controls, the same discipline as
 * the marketing mobile nav.
 */
export function AccountMenu({ name }: { name: string }) {
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
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!panelRef.current?.contains(target) && !buttonRef.current?.contains(target)) {
        setOpen(false);
      }
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointerDown);
    panelRef.current?.querySelector("a")?.focus();
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointerDown);
    };
  }, [open]);

  const initial = (name.trim() || "?").slice(0, 1).toUpperCase();
  const itemClass =
    "block rounded-md px-3 py-2 text-left text-sm text-ink transition-colors hover:bg-field";

  return (
    <div className="relative">
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls="account-menu-panel"
        aria-label={accountCopy.menuLabel}
        onClick={() => setOpen((value) => !value)}
        className="flex h-8 w-8 items-center justify-center rounded-full border border-line bg-card font-display text-sm text-ink hover:border-pine"
      >
        {initial}
      </button>
      {open ? (
        <div
          id="account-menu-panel"
          ref={panelRef}
          className="absolute right-0 top-10 z-40 w-56 rounded-lg border border-line bg-card p-1 shadow-lg"
        >
          <Link href="/app/account" onClick={() => setOpen(false)} className={itemClass}>
            {accountCopy.profileItem}
          </Link>
          <Link
            href="/app/account/organization"
            onClick={() => setOpen(false)}
            className={itemClass}
          >
            {accountCopy.organizationItem}
          </Link>
          <Link href="/app/runs?mine=1" onClick={() => setOpen(false)} className={itemClass}>
            {accountCopy.myRunsItem}
          </Link>
          <Link
            href="/app/settings/notifications"
            onClick={() => setOpen(false)}
            className={itemClass}
          >
            {accountCopy.settingsItem}
          </Link>
          <form method="post" action="/api/auth/sign-out" className="border-t border-line-soft">
            <button type="submit" className={`${itemClass} w-full`}>
              {accountCopy.signOutItem}
            </button>
          </form>
        </div>
      ) : null}
    </div>
  );
}
