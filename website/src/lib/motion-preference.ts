import { useSyncExternalStore } from "react";

/**
 * The visitor's own switch for the page's decorative motion (the etched
 * field behind the hero figure). Kept for the visit in sessionStorage; the
 * OS reduced-motion preference is honored separately and always wins.
 * A store rather than context so the footer control and the field share
 * one value without lifting state into the page.
 */
const KEY = "convoy:motion-paused";
const listeners = new Set<() => void>();
/** The choice made on this page. Authoritative once set; storage only carries it across a reload. */
let memory: boolean | null = null;

export function isMotionPaused(): boolean {
  if (memory !== null) return memory;
  try {
    return window.sessionStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

export function setMotionPaused(paused: boolean) {
  memory = paused;
  try {
    if (paused) window.sessionStorage.setItem(KEY, "1");
    else window.sessionStorage.removeItem(KEY);
  } catch {
    // Storage unavailable (private mode, blocked, quota): the choice still holds for this page.
  }
  listeners.forEach((listener) => listener());
}

export function subscribeMotionPaused(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Server-rendered as "not paused"; the stored choice applies once hydrated. */
export function useMotionPaused(): boolean {
  return useSyncExternalStore(subscribeMotionPaused, isMotionPaused, () => false);
}
