"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { copy } from "@/lexicon";

/**
 * The bell: a checkpoints-and-alerts panel on every portal page. W0 renders
 * the calm state only; the notifier packet later feeds the count badge and
 * panel items through the data slots below (TODO(website-Wn)).
 */
export function BellButton() {
  const [open, setOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const count = 0;

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "Escape" && open) {
      event.preventDefault();
      setOpen(false);
      buttonRef.current?.focus();
    }
  }

  return (
    <div className="relative" onKeyDown={onKeyDown}>
      <button
        ref={buttonRef}
        type="button"
        aria-label="Notifications"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="relative flex h-9 w-9 items-center justify-center rounded-md border border-line text-muted transition-colors hover:text-ink"
      >
        <svg aria-hidden="true" viewBox="0 0 20 20" fill="none" className="h-5 w-5">
          <path
            d="M10 2.5a5 5 0 0 0-5 5v2.9l-1.4 2.8a.6.6 0 0 0 .54.87h11.72a.6.6 0 0 0 .54-.87L15 10.4V7.5a5 5 0 0 0-5-5Z"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinejoin="round"
          />
          <path d="M8.2 16.5a2 2 0 0 0 3.6 0" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
        <span
          data-slot="bell-count"
          hidden={count === 0}
          className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-hold px-1 font-mono text-[10px] text-card"
        >
          {count}
        </span>
      </button>
      {open ? (
        <div
          role="dialog"
          aria-label="Notifications"
          data-slot="bell-panel"
          className="absolute right-0 top-11 z-40 w-72 rounded-lg border border-line bg-card p-4 shadow-lg"
        >
          <p className="text-sm text-muted">{copy.allQuiet}</p>
          <Link
            href="/app/checkpoints"
            onClick={() => setOpen(false)}
            className="mt-3 inline-block text-sm font-medium text-pine-deep hover:text-pine"
          >
            {copy.openCheckpoints}
          </Link>
        </div>
      ) : null}
    </div>
  );
}
