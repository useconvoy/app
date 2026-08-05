import { copy } from "@/lexicon";

export interface HowItFitsStripProps {
  showHowItFits: boolean;
}

/** Plain-language orientation strip on the Routines list. */
export function HowItFitsStrip({ showHowItFits }: HowItFitsStripProps) {
  if (!showHowItFits) return null;
  return (
    <aside
      aria-label={copy.howItFitsTitle}
      className="rounded-lg border border-line bg-card p-4"
    >
      <h2 className="text-sm font-semibold text-ink">{copy.howItFitsTitle}</h2>
      <p className="mt-1 text-sm text-muted">{copy.howItFits}</p>
    </aside>
  );
}
