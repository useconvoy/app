"use client";

import { FOOTER } from "@/content/homepage";
import { setMotionPaused, useMotionPaused } from "@/lib/motion-preference";

/**
 * One discreet control for the page's decorative motion. Pressed means
 * paused: the field stops and its rings are removed at once. The OS
 * reduced-motion setting takes precedence and is not overridden here.
 */
export function MotionToggle({ className = "" }: { className?: string }) {
  const paused = useMotionPaused();
  return (
    <button
      type="button"
      aria-pressed={paused}
      onClick={() => setMotionPaused(!paused)}
      className={`inline-flex min-h-11 items-center gap-2 rounded px-1 text-muted hover:text-primary hover:underline ${className}`}
      data-motion-toggle
    >
      <span aria-hidden="true" className="inline-block h-2 w-2 rounded-full border border-current" style={{ background: paused ? "transparent" : "currentColor" }} />
      {paused ? FOOTER.motion.resume : FOOTER.motion.pause}
    </button>
  );
}
