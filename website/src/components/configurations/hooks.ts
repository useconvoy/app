"use client";

import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";

/**
 * Wall-clock ms, updated every `intervalMs` for relative times ("9s ago").
 * Null on the server and during the first client render, so markup hydrates
 * identically; show the label without an age until it arrives.
 */
export function useNow(intervalMs = 1000): number | null {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    const tick = () => setNow(Date.now());
    const first = window.setTimeout(tick, 0);
    const timer = window.setInterval(tick, intervalMs);
    return () => { window.clearTimeout(first); window.clearInterval(timer); };
  }, [intervalMs]);
  return now;
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
