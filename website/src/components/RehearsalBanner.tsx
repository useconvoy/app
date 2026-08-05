import { copy, terms } from "@/lexicon";

export interface RehearsalBannerProps {
  /** Where leaving rehearsal context navigates to. */
  exitHref: string;
}

/** The persistent frame that makes rehearsal context unmistakable: graphite, dashed. */
export function RehearsalBanner({ exitHref }: RehearsalBannerProps) {
  return (
    <aside
      aria-label={terms.sandbox}
      className="flex flex-wrap items-center justify-between gap-3 border border-dashed border-graphite bg-graphite-soft px-4 py-2 text-sm text-graphite"
    >
      <span>{copy.rehearsalBanner}</span>
      <a
        href={exitHref}
        className="font-mono text-xs font-semibold uppercase tracking-wide text-graphite underline"
      >
        {copy.exitRehearsal}
      </a>
    </aside>
  );
}
