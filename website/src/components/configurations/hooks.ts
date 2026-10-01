"use client";

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { useSearchParams } from "next/navigation";

/** Default page clock: once per live-device poll, so a page re-renders every 15 s, not every second. */
export const PAGE_CLOCK_MS = 15_000;

/**
 * Wall-clock ms for a page, updated every `intervalMs` (coarse by default) for
 * time-based rules and minute-level times ("Updated 3 min ago"). Null on the
 * server and during the first client render, so markup hydrates identically.
 * Labels that count seconds (a measured value's age) tick on their own with
 * `useSecondClock`, so the page does not re-render every second.
 */
export function useNow(intervalMs = PAGE_CLOCK_MS): number | null {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    const tick = () => setNow(Date.now());
    const first = window.setTimeout(tick, 0);
    const timer = window.setInterval(tick, intervalMs);
    return () => { window.clearTimeout(first); window.clearInterval(timer); };
  }, [intervalMs]);
  return now;
}

/* One shared one-second ticker; it runs only while a label subscribes. */
const secondListeners = new Set<() => void>();
let secondTimer: ReturnType<typeof setInterval> | null = null;
let secondNow: number | null = null;
function subscribeSeconds(listener: () => void): () => void {
  secondListeners.add(listener);
  if (secondTimer === null) {
    secondNow = Date.now();
    secondTimer = setInterval(() => { secondNow = Date.now(); for (const item of [...secondListeners]) item(); }, 1000);
  }
  return () => {
    secondListeners.delete(listener);
    if (!secondListeners.size && secondTimer !== null) { clearInterval(secondTimer); secondTimer = null; secondNow = null; }
  };
}
const noSubscribe = () => () => undefined;
const noTime = () => null;

/**
 * Wall-clock ms updated every second for one small component (a relative-time
 * label), so only that label re-renders; null while `enabled` is false, on the
 * server and during hydration.
 */
export function useSecondClock(enabled = true): number | null {
  return useSyncExternalStore(enabled ? subscribeSeconds : noSubscribe, enabled ? () => secondNow : noTime, noTime);
}

/**
 * One query parameter as state (tabs, `?trace=`, `?rollout=`). Updates use the
 * native history API, which the App Router syncs without a server round trip.
 * `push` (default) keeps Back working for drawers and tabs; pass `{ replace: true }`
 * for incidental state such as a search field.
 * Pages that call this are wrapped in <Suspense> by their route.
 */
export function useQueryState(name: string): [string | null, (value: string | null, options?: { replace?: boolean }) => void] {
  const params = useSearchParams();
  const value = params.get(name);
  const set = useCallback((next: string | null, options: { replace?: boolean } = {}) => {
    const search = new URLSearchParams(window.location.search);
    if (next === null || next === "") search.delete(name); else search.set(name, next);
    const query = search.toString();
    const url = `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`;
    if (options.replace) window.history.replaceState(null, "", url); else window.history.pushState(null, "", url);
  }, [name]);
  return [value, set];
}
