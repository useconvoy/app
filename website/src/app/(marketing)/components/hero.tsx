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
          {/* Full width until there is room for a row.
              The two labels need 361px side by side, which fits at 402px and
              nowhere below it, so on every phone narrower than a Pro Max they
              wrapped into two buttons of different widths with a ragged edge
              down the right. Stretching them is what every comparable hero
              does; none ships a stacked pair at differing content widths. */}
          <div className="mb-6 flex flex-col gap-3 sm:mb-9 sm:flex-row sm:gap-3.5">
            <Link
              href="/demo"
              className="inline-flex w-full items-center justify-center rounded-[10px] bg-pine px-5 py-3 text-[14.5px] font-semibold text-card-alt transition-[background-color] duration-75 hover:bg-pine-deep sm:w-auto sm:justify-start"
            >
              Request a demo
            </Link>
            <a
              href="#lifecycle"
              className="inline-flex w-full items-center justify-center rounded-[10px] border border-line px-5 py-3 text-[14.5px] font-semibold text-ink transition-[border-color] duration-200 ease-[var(--ease-entrance)] hover:border-muted sm:w-auto sm:justify-start"
            >
              See how a run works →
            </a>
          </div>
          {/* Two by two at every width. A wrapping row orphans the fourth
              item on desktop; a single column on a phone leaves four short
              lines each sitting in a full-width box, which is most of the
              void this block used to open up. A grid can do neither. */}
          <ul className="grid max-w-[30rem] grid-cols-2 gap-x-4 gap-y-3 font-mono text-[11.5px] tracking-[0.06em] sm:gap-x-6 sm:text-xs sm:tracking-[0.08em]">
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
