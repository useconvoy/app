/**
 * Where `/app` lands. The server always redirects `/app` to `/app/projects` (it cannot see
 * the session cookie, which is scoped to `/api`), so the browser decides once per page
 * load: an account with saved workspace configurations goes on to Configurations.
 * Opening Projects from the top bar, or by its own URL, stays on Projects.
 *
 * "pending": this page load came through the `/app` redirect and the account's workspace
 * is not read yet; "leaving": going on to Configurations; "settled": decided.
 */
export type EntryState = "pending" | "leaving" | "settled";

let state: EntryState | null = null;
const listeners = new Set<() => void>();

export function entryState(): EntryState {
  if (typeof window === "undefined") return "settled";
  if (state === null) {
    const navigation = performance.getEntriesByType?.("navigation")[0] as PerformanceNavigationTiming | undefined;
    state = navigation && navigation.redirectCount > 0 && /^\/app\/projects\/?$/.test(window.location.pathname) ? "pending" : "settled";
  }
  return state;
}

export function setEntryState(next: EntryState) {
  if (state === next) return;
  state = next;
  for (const listener of [...listeners]) listener();
}

/** For `useSyncExternalStore(subscribeEntry, entryState, serverEntryState)`. */
export function subscribeEntry(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
export const serverEntryState = (): EntryState => "settled";
