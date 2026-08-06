import Link from "next/link";

/** Where the secondary link points when a page does not say otherwise. */
const DEFAULT_SECONDARY = {
  href: "/platform",
  label: "Explore the platform",
} as const;

/**
 * Closing call-to-action band shared by marketing pages.
 *
 * The secondary link is a prop rather than something derived from the current
 * route, so this stays a server component: reading the pathname would need
 * usePathname, which would push the whole band, on every marketing page, into
 * the client bundle to decide one href. A page that would otherwise link to
 * itself passes its own.
 */
export function CtaBand({
  title = "See a routine run with your own eyes",
  body = "We will walk you through a live rehearsal, checkpoints and all, in about thirty minutes.",
  secondary = DEFAULT_SECONDARY,
}: {
  title?: string;
  body?: string;
  secondary?: { href: string; label: string };
}) {
  return (
    <section
      aria-labelledby="cta-band-heading"
      className="border-t border-line"
    >
      <div className="mx-auto max-w-[1160px] px-5 py-16 sm:px-7 sm:py-20">
        {/* The closing panel is an object on the page rather than another
            full-bleed stripe: after five bands running edge to edge, one that
            stops short is what reads as an ending. */}
        <div className="sheet-shading relative mx-auto max-w-[52rem] rounded-[14px] border border-line bg-card px-6 py-12 text-center sm:px-12">
          <h2
            id="cta-band-heading"
            /* balance: centered text left to itself hangs one or two words on
               a line of their own, which is the most visible kind of ragged. */
            className="font-display text-[clamp(26px,3vw,34px)] leading-[1.15] font-medium tracking-[-0.012em] text-balance text-ink"
          >
            {title}
          </h2>
          <p className="mx-auto mt-4 max-w-[46ch] text-[16.5px] leading-[1.7] text-balance text-muted">
            {body}
          </p>
          <div className="mt-8 flex flex-wrap justify-center gap-3.5">
            <Link
              href="/demo"
              className="inline-flex items-center rounded-[10px] bg-pine px-5 py-3 text-[14.5px] font-semibold text-card-alt transition-[background-color] duration-75 hover:bg-pine-deep"
            >
              Request a demo
            </Link>
            <Link
              href={secondary.href}
              className="inline-flex items-center rounded-[10px] border border-line px-5 py-3 text-[14.5px] font-semibold text-ink transition-[border-color] duration-200 ease-[var(--ease-entrance)] hover:border-muted"
            >
              {secondary.label}
            </Link>
          </div>
        </div>
      </div>
    </section>
  );
}
