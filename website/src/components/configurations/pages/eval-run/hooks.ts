"use client";

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { useSearchParams } from "next/navigation";
import { notifySessionExpired } from "@/lib/configurations/session-events";
import { api, ApiError, errorText } from "@/lib/platform/client";

export type QueryChanges = Readonly<Record<string, string | null>>;
export interface RunQuery {
  get: (name: string) => string | null;
  /** The page URL with these changes applied (null removes a parameter). */
  href: (changes: QueryChanges) => string;
  /** Applies several changes in one history entry: replace by default (filters), push for views such as the replay. */
  update: (changes: QueryChanges, options?: { push?: boolean }) => void;
}

function withChanges(path: string, current: string, changes: QueryChanges): string {
  const search = new URLSearchParams(current);
  for (const [key, value] of Object.entries(changes)) {
    if (value === null || value === "") search.delete(key); else search.set(key, value);
  }
  const query = search.toString();
  return `${path}${query ? `?${query}` : ""}`;
}

/**
 * The run page's query (`rollout`, `compare`, `outcome`, `slice`, `task`) as state.
 * Writes use the native history API, which the App Router syncs without a server round trip.
 */
export function useRunQuery(basePath: string): RunQuery {
  const params = useSearchParams();
  const search = params.toString();
  const get = useCallback((name: string) => new URLSearchParams(search).get(name), [search]);
  const href = useCallback((changes: QueryChanges) => withChanges(basePath, search, changes), [basePath, search]);
  const update = useCallback((changes: QueryChanges, options: { push?: boolean } = {}) => {
    const url = withChanges(window.location.pathname, window.location.search, changes);
    if (options.push) window.history.pushState(null, "", url); else window.history.replaceState(null, "", url);
  }, []);
  return { get, href, update };
}

export type Remote<T> =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; data: T }
  | { status: "error"; message: string; missing: boolean };

/**
 * One GET through the platform proxy (`/api/platform/{path}`), read once per path;
 * `retry` reads it again. A null path stays idle. A 401 ends the session.
 */
export function usePlatformRead<T>(path: string | null): { state: Remote<T>; retry: () => void } {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{ key: string; value: Remote<T> } | null>(null);
  const key = path ? `${path}\n${attempt}` : null;
  useEffect(() => {
    if (!path) return;
    let current = true;
    const id = `${path}\n${attempt}`;
    void api<T>(path).then(data => {
      if (current) setResult({ key: id, value: { status: "ready", data } });
    }).catch((cause: unknown) => {
      if (!current) return;
      if (cause instanceof ApiError && cause.status === 401) notifySessionExpired();
      setResult({ key: id, value: { status: "error", message: errorText(cause), missing: cause instanceof ApiError && cause.status === 404 } });
    });
    return () => { current = false; };
  }, [path, attempt]);
  const state: Remote<T> = !key ? { status: "idle" } : result?.key === key ? result.value : { status: "loading" };
  const retry = useCallback(() => setAttempt(value => value + 1), []);
  return { state, retry };
}

const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";
function subscribeMotion(callback: () => void) {
  const media = window.matchMedia(REDUCED_MOTION);
  media.addEventListener("change", callback);
  return () => media.removeEventListener("change", callback);
}
/** True when the viewer asked for reduced motion (false on the server). */
export function useReducedMotion(): boolean {
  return useSyncExternalStore(subscribeMotion, () => window.matchMedia(REDUCED_MOTION).matches, () => false);
}
