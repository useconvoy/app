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
      className="border-t border-line bg-card"
    >
      <div className="mx-auto max-w-3xl px-6 py-16 text-center">
        <h2
          id="cta-band-heading"
          className="font-display text-3xl font-medium text-ink"
        >
          {title}
        </h2>
        <p className="mx-auto mt-4 max-w-xl text-muted">{body}</p>
        <div className="mt-8 flex justify-center gap-4">
          <Link
            href="/demo"
            className="inline-flex items-center rounded-md bg-pine px-5 py-2.5 text-sm font-medium text-card hover:bg-pine-deep"
          >
            Request a demo
          </Link>
          <Link
            href={secondary.href}
            className="inline-flex items-center rounded-md border border-line bg-card px-5 py-2.5 text-sm font-medium text-ink hover:border-muted"
          >
            {secondary.label}
          </Link>
        </div>
      </div>
    </section>
  );
}
