import Link from "next/link";

import { RunCard } from "./run-card";

/**
 * Substantive copy on the left, a real artifact on the right.
 *
 * Density reads as competence to this buyer. A vast hero with eight words and
 * enormous type reads as consumer software and invites suspicion, so the
 * proof of the claim sits beside the claim rather than far below it.
 *
 * Nothing here is revealed on scroll: it is above the fold, and animating the
 * largest element on first paint delays the metric that measures it.
 */
const TRUST = [
  "Hard budget caps",
  "Human checkpoints",
  "Attributed record",
  "Runs for days, not minutes",
];

export function Hero() {
  return (
    <header className="pt-14 pb-16 sm:pt-20 sm:pb-24">
      {/* Three percent of ink falling down the measure. One gradient stop, and
          it is the difference between a flat fill and a sheet of stock. */}
      <div className="sheet-shading mx-auto grid max-w-[1160px] grid-cols-[repeat(auto-fit,minmax(min(100%,460px),1fr))] items-center gap-11 px-5 sm:px-7 lg:gap-16">
        <div>
          {/* No eyebrow. The headline says it, and a label above a line that
              short only crowds it. */}
          <h1 className="mb-6 font-display text-[clamp(42px,5.4vw,70px)] leading-[1.05] font-medium tracking-[-0.012em] text-ink">
            {/* The space before the break is load-bearing. A <br> contributes
                nothing to the accessible name, so without it a screen reader
                announces "Routine work,done carefully." as one word. */}
            Routine work,{" "}
            <br />
            <em className="font-medium italic">done carefully.</em>
          </h1>
          <p className="mb-8 max-w-[52ch] text-[18.5px] leading-[1.68] text-muted">
            Convoy runs the routines your team repeats every close and every
            quarter. It rehearses each one before anything is real, and holds
            for your judgment at every step that matters.
          </p>
          <div className="mb-9 flex flex-wrap gap-3.5">
            <Link
              href="/demo"
              className="inline-flex items-center rounded-[10px] bg-pine px-5 py-3 text-[14.5px] font-semibold text-card-alt transition-[background-color] duration-75 hover:bg-pine-deep"
            >
              Request a demo
            </Link>
            <a
              href="#lifecycle"
              className="inline-flex items-center rounded-[10px] border border-line px-5 py-3 text-[14.5px] font-semibold text-ink transition-[border-color] duration-200 ease-[var(--ease-entrance)] hover:border-muted"
            >
              See how a run works →
            </a>
          </div>
          {/* Two by two rather than a wrapping row. Four items on one line is
              a line too long at this measure and the fourth falls to a line of
              its own, which reads as an accident. A grid cannot orphan. */}
          <ul className="grid max-w-[30rem] grid-cols-1 gap-x-6 gap-y-3 font-mono text-xs tracking-[0.08em] sm:grid-cols-2">
            {TRUST.map((item) => (
              <li
                key={item}
                className="border-l-2 border-rule pl-2.5 leading-snug font-medium text-ink"
              >
                {item}
              </li>
            ))}
          </ul>
        </div>

        <RunCard />
      </div>
    </header>
  );
}
