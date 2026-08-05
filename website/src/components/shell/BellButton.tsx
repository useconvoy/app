"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { BellPanelList, type BellPanelItem } from "@/components/BellPanelList";
import { bellSummary, notificationClassGroups } from "@/lexicon";

/**
 * The bell: a checkpoints-and-alerts panel on every portal page, fed by the
 * notifier through /api/notifications. The badge counts unread rows; the
 * panel header sums them by group ("3 held · 1 alert"); every item deep
 * links to its typed action via the notifier-written cta_url. Opening the
 * panel marks the shown unread items read.
 */

interface InboxItem {
  id: string;
  notificationClass: string;
  title: string;
  ctaUrl: string;
  runId: string;
  createdAt: string;
  readAt: string | null;
}

interface Inbox {
  unread: InboxItem[];
  recent: InboxItem[];
}

const POLL_MS = 30_000;
const PANEL_LIMIT = 12;

function groupCounts(items: InboxItem[]): { held: number; alert: number; update: number } {
  const counts = { held: 0, alert: 0, update: 0 };
  for (const item of items) {
    counts[notificationClassGroups[item.notificationClass] ?? "update"] += 1;
  }
  return counts;
}

function toPanelItem(item: InboxItem): BellPanelItem {
  return {
    id: item.id,
    notificationClass: item.notificationClass,
    title: item.title,
    actionUrl: item.ctaUrl,
    at: item.createdAt,
  };
}

export function BellButton() {
  const [open, setOpen] = useState(false);
  const [inbox, setInbox] = useState<Inbox>({ unread: [], recent: [] });
  /** Snapshot taken when the panel opens, so marking read keeps it stable. */
  const [panel, setPanel] = useState<{ summary: string; items: BellPanelItem[] } | null>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  const refresh = useCallback(async () => {
    try {
      const response = await fetch("/api/notifications", { cache: "no-store" });
      if (!response.ok) return;
      setInbox((await response.json()) as Inbox);
    } catch {
      // Keep the last known inbox; the next poll retries.
    }
  }, []);

  useEffect(() => {
    // The first fetch rides a zero timeout so the effect body itself stays
    // free of state updates; the light 30s poll keeps the badge honest.
    const initial = setTimeout(() => void refresh(), 0);
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => {
      clearTimeout(initial);
      clearInterval(timer);
    };
  }, [refresh]);

  const count = inbox.unread.length;

  function toggle() {
    if (open) {
      setOpen(false);
      return;
    }
    const items = [...inbox.unread, ...inbox.recent].slice(0, PANEL_LIMIT).map(toPanelItem);
    setPanel({ summary: bellSummary(groupCounts(inbox.unread)), items });
    setOpen(true);
    const ids = inbox.unread.map((item) => item.id);
    if (ids.length > 0) {
      void fetch("/api/notifications/read", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ ids }),
      })
        .then(() => refresh())
        .catch(() => undefined);
    }
  }

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
        onClick={toggle}
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
          className="absolute right-0 top-11 z-40 w-80 rounded-lg border border-line bg-card shadow-lg"
        >
          {panel && panel.summary.length > 0 ? (
            <p className="border-b border-line-soft px-4 py-2 font-mono text-[11px] uppercase tracking-wide text-muted">
              {panel.summary}
            </p>
          ) : null}
          <BellPanelList items={panel?.items ?? []} checkpointsUrl="/app/checkpoints" />
        </div>
      ) : null}
    </div>
  );
}
