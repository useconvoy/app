"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

export type TracePhase = "idle" | "playing" | "done" | "emphasis";

const REDUCED = "(prefers-reduced-motion: reduce)";

/**
 * Lifecycle for a one-shot diagram sequence driven by CSS keyframes.
 *
 * - Plays once when the start marker (startRef) enters the viewport with
 *   room below it (entryMargin trims the bottom of the viewport; a figure
 *   that should be well in view before it plays uses a larger margin),
 *   unless the person prefers reduced motion or has
 *   asked to save data. Any activation, including the control, uses up that
 *   one autoplay, so an interrupted run never restarts on re-entry.
 * - `play()` restarts from the beginning; several calls in one frame collapse
 *   to one run with one end timer. Each activation carries a run id, the
 *   pending frame is tracked and cancelled, and a frame or timeout from a
 *   superseded run does nothing.
 * - A run is cancelled, back to the complete static diagram, when the root
 *   (rootRef) leaves the viewport entirely, the document is hidden, the
 *   reduced-motion preference changes, the viewport is resized or rotated
 *   during a run, or the component unmounts.
 * - Under reduced motion `play()` toggles a static "emphasis" state instead
 *   of animating; nothing moves and no timer runs.
 *
 * No requestAnimationFrame loop, no idle timer: one frame to restart, one
 * timeout to end.
 */
export function useTrace(durationMs: number, { entryMargin = "-15%" }: { entryMargin?: string } = {}) {
  const [phase, setPhase] = useState<TracePhase>("idle");
  const rootRef = useRef<HTMLDivElement>(null);
  const startRef = useRef<HTMLDivElement>(null);
  const timer = useRef<number | null>(null);
  const frame = useRef<number | null>(null);
  const run = useRef(0);
  const autoplayed = useRef(false);

  const clearPending = useCallback(() => {
    if (frame.current !== null) {
      window.cancelAnimationFrame(frame.current);
      frame.current = null;
    }
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  const prefersReduced = () => window.matchMedia(REDUCED).matches;
  const saveData = () => {
    const nav = navigator as Navigator & { connection?: { saveData?: boolean } };
    return Boolean(nav.connection?.saveData);
  };

  const cancel = useCallback(() => {
    run.current += 1;
    clearPending();
    setPhase((current) => (current === "playing" || current === "emphasis" ? "idle" : current));
  }, [clearPending]);

  const play = useCallback(() => {
    autoplayed.current = true;
    const id = (run.current += 1);
    clearPending();
    if (document.visibilityState === "hidden") return;
    if (prefersReduced()) {
      setPhase((current) => (current === "emphasis" ? "done" : "emphasis"));
      return;
    }
    setPhase("idle");
    frame.current = window.requestAnimationFrame(() => {
      frame.current = null;
      if (run.current !== id) return;
      if (document.visibilityState === "hidden" || prefersReduced()) return;
      setPhase("playing");
      timer.current = window.setTimeout(() => {
        timer.current = null;
        if (run.current !== id) return;
        setPhase("done");
      }, durationMs);
    });
  }, [clearPending, durationMs]);

  // Autoplay once when the start marker is in view with room below it.
  useEffect(() => {
    const start = startRef.current;
    if (!start || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry.isIntersecting || autoplayed.current) return;
        if (prefersReduced() || saveData()) return;
        autoplayed.current = true;
        play();
      },
      { rootMargin: `0px 0px ${entryMargin} 0px`, threshold: 0 },
    );
    observer.observe(start);
    return () => observer.disconnect();
  }, [play, entryMargin]);

  // Cancel only when the whole root is out of view.
  useEffect(() => {
    const root = rootRef.current;
    if (!root || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry.isIntersecting) cancel();
      },
      { threshold: 0 },
    );
    observer.observe(root);
    return () => observer.disconnect();
  }, [cancel]);

  // The motion preference, read from the media query; a change cancels a
  // run at once.
  const reduced = useSyncExternalStore(
    (onChange) => {
      const media = window.matchMedia(REDUCED);
      const handle = () => {
        onChange();
        cancel();
      };
      media.addEventListener("change", handle);
      return () => media.removeEventListener("change", handle);
    },
    () => window.matchMedia(REDUCED).matches,
    () => false,
  );

  // A resize or rotation during a run settles back to the static diagram.
  useEffect(() => {
    if (phase !== "playing") return;
    const onResize = () => cancel();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [phase, cancel]);

  // A hidden document cancels; unmount clears everything.
  useEffect(() => {
    const onVisibility = () => {
      if (document.visibilityState === "hidden") cancel();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      run.current += 1;
      clearPending();
    };
  }, [cancel, clearPending]);

  return { phase, play, reduced, rootRef, startRef };
}
