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
      <div className="mx-auto grid max-w-[1160px] grid-cols-[repeat(auto-fit,minmax(min(100%,460px),1fr))] items-center gap-11 px-5 sm:px-7 lg:gap-16">
        <div>
          <p className="mb-5 font-mono text-xs font-semibold tracking-[0.17em] text-muted uppercase">
            Durable routines for regulated operations
          </p>
          <h1 className="mb-6 font-display text-[clamp(42px,5.4vw,70px)] leading-[1.05] font-medium tracking-[-0.012em] text-ink">
            Delegate the work.
            <br />
            <em className="font-medium italic">Keep control.</em>
          </h1>
          <p className="mb-8 max-w-[56ch] text-[18.5px] leading-[1.68] text-muted">
            Convoy runs specialized routines that carry recurring operational
            work from start to finish. Every run is planned up front, approved
            by you, and recorded step by step, even when the job takes days.
            Built first for audit and compliance teams.
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
          <ul className="flex flex-wrap gap-x-6 gap-y-2.5 font-mono text-xs tracking-[0.08em]">
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
